"""
Advanced SolidWorks tools (stage 1): see, measure, planes, 3D paths,
sweep / loft / revolve, editable dimensions, feature housekeeping.

Author: 906grown

Every SolidWorks call here was verified live against SOLIDWORKS 2026
(API v34) before being written. Late-bound COM exposes zero-argument
methods as properties, so `_g()` is used for those.
"""

import base64
import json
import logging
import math
import os
import struct
import tempfile
import traceback
import zlib

import pythoncom
import win32com.client
from mcp.types import Tool, TextContent, ImageContent

logger = logging.getLogger("SolidWorksMCP.advanced")

V = win32com.client.VARIANT
UNITS = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254, "inch": 0.0254}
VIEWS = {"front": 1, "back": 2, "left": 3, "right": 4, "top": 5, "bottom": 6,
         "isometric": 7, "trimetric": 8, "dimetric": 9}
NAME_TYPES = ["PLANE", "SKETCH", "BODYFEATURE", "AXIS", "DATUMPOINT", "REFCURVE",
              "EDGE", "FACE", "VERTEX", "SKETCHSEGMENT", "EXTSKETCHSEGMENT",
              "SKETCHPOINT", "EXTSKETCHPOINT", "COMPONENT"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _none():
    return V(pythoncom.VT_DISPATCH, None)


def _arr(vals):
    return V(pythoncom.VT_ARRAY | pythoncom.VT_R8, [float(v) for v in vals])


def _g(obj, name, *args):
    """Read a member whether COM exposes it as a property or a method."""
    a = getattr(obj, name)
    if args:
        return a(*args)
    if callable(a) and not hasattr(a, "_oleobj_"):
        return a()
    return a


def _u(unit):
    return UNITS.get((unit or "mm").lower(), 0.001)


def _mm(v):
    return round(v * 1000.0, 4)


def _vec(v):
    return [round(x, 5) for x in v]


def _xf_point(a, p, vector=False):
    """Apply a SolidWorks MathTransform ArrayData to a point/vector."""
    R, T, sc = a[0:9], a[9:12], a[12]
    out = [p[0] * R[0] + p[1] * R[3] + p[2] * R[6],
           p[0] * R[1] + p[1] * R[4] + p[2] * R[7],
           p[0] * R[2] + p[1] * R[5] + p[2] * R[8]]
    if vector:
        return out
    return [sc * out[0] + T[0], sc * out[1] + T[1], sc * out[2] + T[2]]


def _ok(msg, data=None):
    return {"success": True, "message": msg, "error_code": 0, "error_name": "swSuccess", "data": data or {}}


def _err(msg, data=None):
    return {"success": False, "message": msg, "error_code": 999, "error_name": "swError", "data": data or {}}


class Ctx:
    def __init__(self, automation):
        self.auto = automation

    @property
    def sw(self):
        if not self.auto.is_connected:
            r = self.auto.connect()
            if not r["success"]:
                raise RuntimeError(r["message"])
        return self.auto.app

    def doc(self):
        d = self.sw.ActiveDoc
        if d is None:
            raise RuntimeError("No document is open in SolidWorks.")
        return d

    def select(self, d, name, typ=None, append=False, mark=0):
        ext = d.Extension
        if name in ("Front", "Top", "Right"):
            name += " Plane"
        types = [typ.upper()] if typ else NAME_TYPES
        for t in types:
            if ext.SelectByID2(name, t, 0, 0, 0, append, mark, _none(), 0):
                return t
        raise RuntimeError(f"Could not select '{name}'" + (f" as {typ}" if typ else "") + ".")

    def select_obj(self, d, obj, mark=0, append=True):
        sd = _g(d.SelectionManager, "CreateSelectData")
        sd.Mark = mark
        return obj.Select4(append, sd)

    @staticmethod
    def last_feature(d):
        return d.FeatureByPositionReverse(0)


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------

def _bmp_to_png(bmp_path, max_w):
    data = open(bmp_path, "rb").read()
    off = struct.unpack_from("<I", data, 10)[0]
    w, h = struct.unpack_from("<ii", data, 18)
    bypp = struct.unpack_from("<H", data, 28)[0] // 8
    stride = (w * bypp + 3) & ~3
    step = max(1, -(-w // max_w))
    ow, oh = w // step, abs(h) // step
    rows = []
    for oy in range(oh):
        y = oy * step
        sy = (abs(h) - 1 - y) if h > 0 else y
        base = off + sy * stride
        row = bytearray(b"\x00")
        for ox in range(ow):
            p = base + ox * step * bypp
            row += bytes((data[p + 2], data[p + 1], data[p]))
        rows.append(bytes(row))

    def chunk(t, c):
        return struct.pack(">I", len(c)) + t + c + struct.pack(">I", zlib.crc32(t + c) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", ow, oh, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 6))
            + chunk(b"IEND", b""))


def take_screenshot(c, a):
    d = c.doc()
    view = (a.get("view") or "current").lower()
    if view in VIEWS:
        d.ShowNamedView2("*" + view.capitalize(), VIEWS[view])
    if a.get("zoom_fit", view != "current"):
        d.ViewZoomtofit2()
    if a.get("clear_selection", False):
        d.ClearSelection2(True)
    w = int(a.get("width", 1100))
    bmp = os.path.join(tempfile.gettempdir(), "sw_mcp_view.bmp")
    if not d.SaveBMP(bmp, w, int(w * 0.72)):
        return _err("SolidWorks could not capture the view.")
    png = _bmp_to_png(bmp, w)
    title = _g(d, "GetTitle")
    return [ImageContent(type="image", data=base64.b64encode(png).decode(), mimeType="image/png"),
            TextContent(type="text", text=f"[SUCCESS] Screenshot of {title} ({view} view)")]


# ---------------------------------------------------------------------------
# Inspect / measure
# ---------------------------------------------------------------------------

def _surface_info(face, xf=None):
    s = _g(face, "GetSurface")
    info = {"area_mm2": round(_g(face, "GetArea") * 1e6, 3)}
    P = (lambda p: _xf_point(xf, p)) if xf else (lambda p: p)
    Vv = (lambda v: _xf_point(xf, v, True)) if xf else (lambda v: v)
    if _g(s, "IsCylinder"):
        cp = s.CylinderParams
        info.update(kind="cylinder", diameter_mm=_mm(cp[6] * 2), radius_mm=_mm(cp[6]),
                    axis_point_mm=[_mm(v) for v in P(cp[0:3])], axis_dir=_vec(Vv(cp[3:6])))
    elif _g(s, "IsPlane"):
        pp = s.PlaneParams
        info.update(kind="plane", normal=_vec(Vv(pp[0:3])), point_mm=[_mm(v) for v in P(pp[3:6])])
    elif _g(s, "IsCone"):
        cp = s.ConeParams
        info.update(kind="cone", axis_point_mm=[_mm(v) for v in P(cp[0:3])], axis_dir=_vec(Vv(cp[3:6])),
                    radius_at_point_mm=_mm(cp[6]), half_angle_deg=round(math.degrees(cp[7]), 3))
    elif _g(s, "IsSphere"):
        sp = s.SphereParams
        info.update(kind="sphere", center_mm=[_mm(v) for v in P(sp[0:3])], radius_mm=_mm(sp[3]))
    elif _g(s, "IsTorus"):
        info.update(kind="torus")
    else:
        info.update(kind="freeform")
    try:
        box = _g(face, "GetBox")
        lo, hi = P(box[0:3]), P(box[3:6])
        info["bbox_mm"] = [[_mm(min(lo[i], hi[i])) for i in range(3)], [_mm(max(lo[i], hi[i])) for i in range(3)]]
    except Exception:
        pass
    return info


def _edge_info(edge, xf=None):
    cv = _g(edge, "GetCurve")
    P = (lambda p: _xf_point(xf, p)) if xf else (lambda p: p)
    Vv = (lambda v: _xf_point(xf, v, True)) if xf else (lambda v: v)
    info = {}
    if _g(cv, "IsCircle"):
        cp = cv.CircleParams
        info.update(kind="circle/arc", center_mm=[_mm(v) for v in P(cp[0:3])], axis_dir=_vec(Vv(cp[3:6])),
                    diameter_mm=_mm(cp[6] * 2))
    elif _g(cv, "IsLine"):
        lp = cv.LineParams
        info.update(kind="line", point_mm=[_mm(v) for v in P(lp[0:3])], dir=_vec(Vv(lp[3:6])))
    else:
        info.update(kind="curve")
    try:
        cp = _g(edge, "GetCurveParams3")
        sp, ep = cp.StartPoint, cp.EndPoint
        info["start_mm"] = [_mm(v) for v in P(sp[0:3])]
        info["end_mm"] = [_mm(v) for v in P(ep[0:3])]
        info["length_mm"] = _mm(cv.GetLength3(cp.UMinValue, cp.UMaxValue))
    except Exception:
        pass
    return info


def inspect_selection(c, a):
    d = c.doc()
    sm = d.SelectionManager
    n = sm.GetSelectedObjectCount2(-1)
    if n == 0:
        return _err("Nothing is selected. Click a face, edge or point in SolidWorks (Ctrl+click for several), then try again.")
    items = []
    for i in range(1, n + 1):
        t = sm.GetSelectedObjectType3(i, -1)
        o = sm.GetSelectedObject6(i, -1)
        xf, comp_name = None, None
        try:
            comp = sm.GetSelectedObjectsComponent4(i, -1)
            if comp is not None:
                comp_name = comp.Name2
                xf = comp.Transform2.ArrayData
        except Exception:
            pass
        item = {"index": i, "sel_type": t}
        if comp_name:
            item["component"] = comp_name
        try:
            if t == 2:
                item["type"] = "face"; item.update(_surface_info(o, xf))
            elif t == 1:
                item["type"] = "edge"; item.update(_edge_info(o, xf))
            elif t == 3:
                p = _g(o, "GetPoint"); p = _xf_point(xf, p) if xf else p
                item.update(type="vertex", point_mm=[_mm(v) for v in p])
            elif t in (4, 5, 22, 11):
                item.update(type="feature/reference", name=getattr(o, "Name", "?"))
            elif t == 20:
                item.update(type="component", name=o.Name2)
            else:
                item["type"] = "other"
        except Exception as e:
            item["error"] = str(e)
        items.append(item)
    m = {}
    try:
        meas = _g(d.Extension, "CreateMeasure")
        if meas.Calculate(_none()):
            for k in ["Distance", "CenterDistance", "NormalDistance", "Angle", "Diameter", "Radius",
                      "Length", "Area", "Perimeter", "ArcLength", "DeltaX", "DeltaY", "DeltaZ"]:
                try:
                    v = getattr(meas, k)
                    if v is not None and v != -1:
                        if k == "Angle":
                            m["angle_deg"] = round(math.degrees(v), 4)
                        elif k in ("Area",):
                            m["area_mm2"] = round(v * 1e6, 3)
                        else:
                            m[k[0].lower() + k[1:] + "_mm"] = _mm(v)
                except Exception:
                    pass
    except Exception:
        pass
    return _ok(f"{n} selected item(s)", {"items": items, "measure": m, "units": "mm / degrees, model coordinates"})


def list_round_faces(c, a):
    d = c.doc()
    if _g(d, "GetType") != 1:
        return _err("This works on a part. Open or activate the part first.")
    lo = float(a.get("min_diameter", 0)); hi = float(a.get("max_diameter", 1e9))
    out = []
    for b in d.GetBodies2(0, True) or []:
        for f in _g(b, "GetFaces") or []:
            info = _surface_info(f)
            if info["kind"] in ("cylinder", "cone"):
                dia = info.get("diameter_mm", info.get("radius_at_point_mm", 0) * 2)
                if lo <= dia <= hi:
                    out.append(info)
    out.sort(key=lambda x: x.get("diameter_mm", 0))
    return _ok(f"{len(out)} round face(s)", {"faces": out})


def get_bounding_box(c, a):
    d = c.doc()
    box = d.GetPartBox(True) if _g(d, "GetType") == 1 else d.GetBox(0)
    lo, hi = [_mm(v) for v in box[0:3]], [_mm(v) for v in box[3:6]]
    return _ok("Bounding box (mm)", {"min": lo, "max": hi, "size": [round(hi[i] - lo[i], 4) for i in range(3)]})


# ---------------------------------------------------------------------------
# Documents / selection / housekeeping
# ---------------------------------------------------------------------------

def activate_document(c, a):
    name = a["name"]
    sw = c.sw
    d = None
    for nm in (name, name + ".SLDPRT", name + ".SLDASM"):
        try:
            d = sw.ActivateDoc(nm)
        except Exception:
            d = None
        if d is not None:
            break
    if d is None:
        return _err(f"Could not activate '{name}'. Use list_open_documents to see exact names.")
    return _ok(f"Active document: {_g(sw.ActiveDoc, 'GetTitle')}")


def select_entity(c, a):
    d = c.doc()
    t = c.select(d, a["name"], a.get("type"), bool(a.get("append", False)), int(a.get("mark", 0)))
    return _ok(f"Selected {a['name']} ({t})")


def select_at_point(c, a):
    d = c.doc(); k = _u(a.get("unit"))
    typ = (a.get("type") or "FACE").upper()
    ok = d.Extension.SelectByID2("", typ, a["x"] * k, a["y"] * k, a["z"] * k,
                                 bool(a.get("append", False)), int(a.get("mark", 0)), _none(), 0)
    return _ok(f"Selected {typ} at point") if ok else _err(f"No {typ} found at that point.")


def clear_selection(c, a):
    c.doc().ClearSelection2(True)
    return _ok("Selection cleared")


def rename_feature(c, a):
    d = c.doc(); f = d.FeatureByName(a["name"])
    if f is None:
        return _err(f"No feature named '{a['name']}'.")
    f.Name = a["new_name"]
    return _ok(f"Renamed to {f.Name}")


def delete_feature(c, a):
    d = c.doc(); c.select(d, a["name"])
    ok = d.Extension.DeleteSelection2(0)
    return _ok(f"Deleted {a['name']}") if ok else _err(f"Could not delete {a['name']}.")


def undo(c, a):
    c.doc().EditUndo2(int(a.get("steps", 1)))
    return _ok("Undone")


def rebuild(c, a):
    ok = c.doc().ForceRebuild3(False)
    return _ok("Rebuilt") if ok else _err("Rebuild reported errors - check the feature tree.")


# ---------------------------------------------------------------------------
# Planes and sketches
# ---------------------------------------------------------------------------

def _seg_ends(seg):
    """[start, end] SketchPoints of a line/arc/spline segment."""
    try:
        return [_g(seg, "GetStartPoint2"), _g(seg, "GetEndPoint2")]
    except Exception:
        pts = _g(seg, "GetPoints2")
        return [pts[0], pts[-1]]


def _sk_to_model(sk, p):
    """SketchPoint -> model coordinates (2D sketch points are in sketch space)."""
    xyz = [p.X, p.Y, p.Z]
    try:
        if _g(sk, "Is3D"):
            return xyz
    except Exception:
        pass
    a = list(sk.ModelToSketchTransform.ArrayData)
    R, T, sc = a[0:9], a[9:12], a[12]
    q = [(xyz[j] - T[j]) / sc for j in range(3)]
    return [sum(q[j] * R[i * 3 + j] for j in range(3)) for i in range(3)]


def _chain_ends(sk):
    """Sketch points used by exactly one segment end (open-chain ends)."""
    ends = {}
    for seg in sk.GetSketchSegments or []:
        try:
            pair = _seg_ends(seg)
        except Exception:
            continue
        for p in pair:
            key = (round(p.X, 7), round(p.Y, 7), round(p.Z, 7))
            ends.setdefault(key, []).append((p, seg))
    chain_ends = [v[0] for v in ends.values() if len(v) == 1]
    allpts = dict(ends)
    for seg in sk.GetSketchSegments or []:   # spline through-points for near_point
        try:
            for p in _g(seg, "GetPoints2"):
                key = (round(p.X, 7), round(p.Y, 7), round(p.Z, 7))
                allpts.setdefault(key, []).append((p, seg))
        except Exception:
            pass
    return chain_ends, allpts


def create_plane(c, a):
    d = c.doc(); fm = d.FeatureManager; k = _u(a.get("unit"))
    mode = a.get("mode", "offset")
    flip = 256 if a.get("flip") else 0
    d.ClearSelection2(True)
    if mode == "offset":
        if a.get("reference", "selected") != "selected":
            c.select(d, a["reference"], None, False, 0)
        pl = fm.InsertRefPlane(8 | flip, float(a.get("distance", 0)) * k, 0, 0, 0, 0)
    elif mode == "angle":
        c.select(d, a["reference"], None, False, 0)
        c.select(d, a["axis"], None, True, 1)
        pl = fm.InsertRefPlane(16 | flip, math.radians(float(a.get("angle", 0))), 4, 0, 0, 0)
    elif mode == "normal_to_path":
        f = d.FeatureByName(a["path"])
        if f is None:
            return _err(f"No sketch named '{a['path']}'.")
        sk = f.GetSpecificFeature2
        ends, allpts = _chain_ends(sk)
        target = None
        if a.get("near_point"):
            q = [v * k for v in a["near_point"]]
            pts = [lst[0] for lst in allpts.values()]
            target = min(pts, key=lambda pr: math.dist(_sk_to_model(sk, pr[0]), q))
        else:
            if not ends:
                return _err("Could not find the ends of that path.")
            which = a.get("end", "start")
            first_seg = sk.GetSketchSegments[0]
            sp = _seg_ends(first_seg)[0]
            ends.sort(key=lambda pr: (pr[0].X - sp.X) ** 2 + (pr[0].Y - sp.Y) ** 2 + (pr[0].Z - sp.Z) ** 2)
            target = ends[0] if which == "start" else ends[-1]
        pt, seg = target
        c.select_obj(d, seg, 0, False)
        c.select_obj(d, pt, 1, True)
        pl = fm.InsertRefPlane(2, 0, 4, 0, 0, 0)
    else:
        return _err("mode must be offset, angle, or normal_to_path")
    if pl is None:
        return _err("SolidWorks could not create that plane (check the references).")
    if a.get("name"):
        pl.Name = a["name"]
    return _ok(f"Created plane {pl.Name}", {"name": pl.Name})


def create_sketch_on(c, a):
    d = c.doc(); target = a.get("target", "selected")
    if target != "selected":
        c.select(d, target, None, False, 0)
    d.SketchManager.InsertSketch(True)
    if d.GetActiveSketch2 is None:
        return _err("Could not start a sketch there (select a plane or flat face).")
    return _ok(f"Sketch open on {target}. Draw, then close_sketch.")


def create_3d_path(c, a):
    """Build a 3D sketch path (model coordinates) from spline/line segments."""
    d = c.doc(); sm = d.SketchManager; k = _u(a.get("unit"))
    segs = a["segments"]
    sm.Insert3DSketch(True)
    made = []
    try:
        prev_end = None
        for s in segs:
            pts = [[v * k for v in p] for p in s["points"]]
            if prev_end is not None and math.dist(prev_end, pts[0]) > 1e-9:
                pts.insert(0, prev_end)
            if s.get("type", "spline") == "line":
                for p0, p1 in zip(pts, pts[1:]):
                    made.append(sm.CreateLine(*p0, *p1))
            else:
                made.append(sm.CreateSpline2(_arr([v for p in pts for v in p]), True))
            prev_end = pts[-1]
    finally:
        d.ClearSelection2(True)
        sm.Insert3DSketch(True)
    f = Ctx.last_feature(d)
    if a.get("name"):
        f.Name = a["name"]
    return _ok(f"Created 3D path {f.Name} ({len(made)} segment(s))", {"name": f.Name})


def draw_circle_at_point(c, a):
    d = c.doc(); sm = d.SketchManager; k = _u(a.get("unit"))
    sk = d.GetActiveSketch2
    if sk is None:
        return _err("Open a sketch first (create_sketch_on).")
    p = [a.get("x", 0) * k, a.get("y", 0) * k, a.get("z", 0) * k]
    q = _xf_point(sk.ModelToSketchTransform.ArrayData, p) if a.get("model_coords", True) else p
    r = float(a["diameter"]) * k / 2
    seg = sm.CreateCircleByRadius(q[0], q[1], 0, r)
    if seg is None:
        return _err("Circle could not be created.")
    data = {"sketch_center_mm": [_mm(q[0]), _mm(q[1])], "plane_offset_mm": _mm(q[2])}
    if a.get("dimension", True):
        sw = c.sw
        old = sw.GetUserPreferenceToggle(10)
        try:
            sw.SetUserPreferenceToggle(10, False)
            d.ClearSelection2(True)
            c.select_obj(d, seg, 0, False)
            dd = d.AddDimension2(q[0] + r, q[1] + r, 0)
            if dd is not None:
                dim = dd.GetDimension2(0)
                if a.get("dim_name"):
                    dim.Name = a["dim_name"]
                data["dimension"] = dim.FullName
        finally:
            sw.SetUserPreferenceToggle(10, old)
            d.ClearSelection2(True)
    if abs(q[2]) > 1e-6:
        data["warning"] = "That point is not on the sketch plane; the circle was projected onto it."
    return _ok(f"Circle d={a['diameter']}", data)


def draw_centerline(c, a):
    d = c.doc(); k = _u(a.get("unit"))
    seg = d.SketchManager.CreateCenterLine(a["x1"] * k, a["y1"] * k, 0, a["x2"] * k, a["y2"] * k, 0)
    return _ok("Centerline drawn") if seg is not None else _err("Centerline failed.")


def draw_spline(c, a):
    d = c.doc(); k = _u(a.get("unit"))
    flat = []
    for p in a["points"]:
        flat += [p[0] * k, p[1] * k, (p[2] * k if len(p) > 2 else 0)]
    seg = d.SketchManager.CreateSpline2(_arr(flat), True)
    return _ok("Spline drawn") if seg is not None else _err("Spline failed.")


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

def sweep(c, a):
    d = c.doc(); fm = d.FeatureManager; k = _u(a.get("unit"))
    cut = bool(a.get("cut", False)); merge = bool(a.get("merge", True))
    dia = a.get("circular_diameter")
    d.ClearSelection2(True)
    if a.get("profile"):
        c.select(d, a["profile"], None, True, 1)
    elif dia is None:
        return _err("Give a profile sketch or a circular_diameter.")
    c.select(d, a["path"], None, True, 4)
    circ = dia is not None and not a.get("profile")
    dm = float(dia) * k if circ else 0
    if cut:
        f = fm.InsertCutSwept5(False, False, 0, False, False, 0, 0, False, 0, 0, 0, 0,
                               True, True, 0, True, False, False, False, circ, dm, 0)
    else:
        f = fm.InsertProtrusionSwept4(False, False, 0, False, False, 0, 0, False, 0, 0, 0, 0,
                                      merge, True, True, 0, True, circ, dm, 0)
    if f is None:
        return _err("Sweep failed. Check that the profile starts on the path and doesn't self-intersect.")
    if a.get("name"):
        f.Name = a["name"]
    return _ok(f"Created {f.Name}", {"name": f.Name})


def loft(c, a):
    d = c.doc(); fm = d.FeatureManager
    cut = bool(a.get("cut", False)); merge = bool(a.get("merge", True))
    d.ClearSelection2(True)
    for p in a["profiles"]:
        c.select(d, p, None, True, 1)
    for g in a.get("guides", []) or []:
        c.select(d, g, None, True, 2)
    if a.get("centerline"):
        c.select(d, a["centerline"], None, True, 4)
    if cut:
        f = fm.InsertCutBlend(False, True, False, 1, 0, 0, False, 0, 0, 0, True, True)
    else:
        f = fm.InsertProtrusionBlend2(False, True, False, 1, 0, 0, 1, 1, True, True,
                                      False, 0, 0, 0, merge, True, True, 0)
    if f is None:
        return _err("Loft failed. Profiles must be listed in order and be closed shapes.")
    if a.get("name"):
        f.Name = a["name"]
    return _ok(f"Created {f.Name}", {"name": f.Name})


def revolve(c, a):
    d = c.doc(); fm = d.FeatureManager
    cut = bool(a.get("cut", False))
    ang = math.radians(float(a.get("angle", 360)))
    f = None
    for axis_mark in (4, 16):
        d.ClearSelection2(True)
        c.select(d, a["sketch"], "SKETCH", False, 0)
        if a.get("axis"):
            c.select(d, a["axis"], None, True, axis_mark)
        f = fm.FeatureRevolve2(True, True, False, cut, bool(a.get("reverse", False)), False, 0, 0, ang, 0,
                               False, False, 0, 0, 0, 0, 0, bool(a.get("merge", True)), True, True)
        if f is not None or not a.get("axis"):
            break
    if f is None:
        return _err("Revolve failed. The sketch needs a centerline (or give axis) and must not cross it.")
    if a.get("name"):
        f.Name = a["name"]
    return _ok(f"Created {f.Name}", {"name": f.Name})


def list_dimensions(c, a):
    d = c.doc()
    names = [a["feature"]] if a.get("feature") else None
    out, seen = [], set()
    f = d.FirstFeature
    while f is not None:
        if names is None or f.Name in names:
            dd = _g(f, "GetFirstDisplayDimension")
            while dd is not None:
                try:
                    dim = dd.GetDimension2(0)
                    if dim.FullName in seen:
                        raise StopIteration
                    seen.add(dim.FullName)
                    v = dim.SystemValue
                    is_ang = False
                    try:
                        is_ang = _g(dd, "Type2") == 3  # swAngularDimension
                    except Exception:
                        pass
                    out.append({"name": dim.FullName,
                                "value": round(math.degrees(v), 4) if is_ang else _mm(v),
                                "unit": "deg" if is_ang else "mm"})
                except (Exception, StopIteration):
                    pass
                dd = f.GetNextDisplayDimension(dd)
        f = _g(f, "GetNextFeature")
    return _ok(f"{len(out)} dimension(s)", {"dimensions": out})


def set_dimension(c, a):
    d = c.doc()
    prm = d.Parameter(a["name"])
    if prm is None:
        return _err(f"No dimension '{a['name']}'. Use list_dimensions for exact names (like D1@Sketch3).")
    unit = (a.get("unit") or "mm").lower()
    v = math.radians(float(a["value"])) if unit in ("deg", "degrees") else float(a["value"]) * _u(unit)
    prm.SystemValue = v
    ok = d.EditRebuild3
    return _ok(f"{a['name']} = {a['value']} {unit}", {"rebuild_ok": bool(ok)})


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def _S(props, req=()):
    return {"type": "object", "properties": props, "required": list(req)}


NUM = {"type": "number"}
STR = {"type": "string"}
BOOL = {"type": "boolean"}
UNIT = {"type": "string", "description": "mm (default), cm, m, in"}
PT = {"type": "array", "items": NUM, "minItems": 3, "maxItems": 3}

TOOLS = [
    ("take_screenshot", "Capture the SolidWorks model view as an image so you can see the model. view: current, isometric, trimetric, front, back, top, bottom, left, right.",
     _S({"view": STR, "zoom_fit": BOOL, "width": NUM, "clear_selection": BOOL}), take_screenshot),
    ("activate_document", "Switch the active SolidWorks window to an open document by title (see list_open_documents).",
     _S({"name": STR}, ["name"]), activate_document),
    ("inspect_selection", "Report exact geometry of what the user has selected in SolidWorks (faces: diameter/axis/normal; edges: circle center/diameter, length; vertices), plus a measurement between selections. All mm, model coordinates.",
     _S({}), inspect_selection),
    ("list_round_faces", "List cylindrical/conical faces of the active part with diameter, axis and position - use to find holes, pins and fittings.",
     _S({"min_diameter": NUM, "max_diameter": NUM}), list_round_faces),
    ("get_bounding_box", "Overall size and extents of the active part (mm).", _S({}), get_bounding_box),
    ("select_entity", "Select a plane/sketch/feature/axis/etc by name. mark: 0 normal, 1 profile, 2 guide, 4 path/centerline.",
     _S({"name": STR, "type": STR, "append": BOOL, "mark": NUM}, ["name"]), select_entity),
    ("select_at_point", "Select the FACE/EDGE/VERTEX at a model-coordinate point.",
     _S({"x": NUM, "y": NUM, "z": NUM, "type": STR, "append": BOOL, "mark": NUM, "unit": UNIT}, ["x", "y", "z"]), select_at_point),
    ("clear_selection", "Clear the selection.", _S({}), clear_selection),
    ("create_plane", "Create a reference plane. mode=offset (reference plane/face name or 'selected', distance), angle (reference plane + axis/edge name, angle deg), normal_to_path (path sketch name, end start|end or near_point [x,y,z]) - the plane is square to the path at that point, ideal for sweep/loft profiles.",
     _S({"mode": STR, "reference": STR, "distance": NUM, "angle": NUM, "axis": STR, "path": STR, "end": STR,
         "near_point": PT, "flip": BOOL, "name": STR, "unit": UNIT}, ["mode"]), create_plane),
    ("create_sketch_on", "Open a 2D sketch on any plane or flat face by name, or on the current selection (target='selected').",
     _S({"target": STR}), create_sketch_on),
    ("create_3d_path", "Create a 3D sketch path from segments in model coordinates, e.g. [{type:'line',points:[[0,0,0],[0,0,20]]},{type:'spline',points:[[0,0,20],[0,-5,50],[0,-30,80]]}]. Consecutive segments are joined. For a smooth bent stem prefer ONE spline, then add straight ends as extrudes off planes normal_to_path. Model coordinates (this user's template is Z-up).",
     _S({"segments": {"type": "array", "items": {"type": "object"}}, "name": STR, "unit": UNIT}, ["segments"]), create_3d_path),
    ("draw_circle_at_point", "In the open 2D sketch, draw a circle centered on a MODEL-coordinate point (e.g. a path end), with a driving diameter dimension (optionally named).",
     _S({"x": NUM, "y": NUM, "z": NUM, "diameter": NUM, "dimension": BOOL, "dim_name": STR, "model_coords": BOOL, "unit": UNIT}, ["diameter"]), draw_circle_at_point),
    ("draw_centerline", "Draw a construction centerline in the open 2D sketch (sketch coordinates) - needed for revolves.",
     _S({"x1": NUM, "y1": NUM, "x2": NUM, "y2": NUM, "unit": UNIT}, ["x1", "y1", "x2", "y2"]), draw_centerline),
    ("draw_spline", "Draw a spline through points in the open sketch (sketch coordinates; [x,y] or [x,y,z]).",
     _S({"points": {"type": "array", "items": {"type": "array", "items": NUM}}, "unit": UNIT}, ["points"]), draw_spline),
    ("sweep", "Sweep along a path sketch. Use profile (sketch name) OR circular_diameter (round profile, no sketch needed). cut=true for a swept cut (e.g. an air hole through a bent stem).",
     _S({"path": STR, "profile": STR, "circular_diameter": NUM, "cut": BOOL, "merge": BOOL, "name": STR, "unit": UNIT}, ["path"]), sweep),
    ("loft", "Loft between profile sketches (in order), optionally along a centerline path and with guide curves. cut=true for a lofted cut.",
     _S({"profiles": {"type": "array", "items": STR}, "centerline": STR, "guides": {"type": "array", "items": STR},
         "cut": BOOL, "merge": BOOL, "name": STR}, ["profiles"]), loft),
    ("revolve", "Revolve a sketch around its centerline (or a named axis). cut=true for a revolved cut (e.g. a socket).",
     _S({"sketch": STR, "axis": STR, "angle": NUM, "cut": BOOL, "reverse": BOOL, "merge": BOOL, "name": STR}, ["sketch"]), revolve),
    ("list_dimensions", "List dimension names and values (all, or for one feature) so they can be changed.",
     _S({"feature": STR}), list_dimensions),
    ("set_dimension", "Change a dimension by full name (e.g. D1@Sketch3 or socket_d@Sketch5) and rebuild. unit mm or deg.",
     _S({"name": STR, "value": NUM, "unit": STR}, ["name", "value"]), set_dimension),
    ("rename_feature", "Rename a feature/sketch/plane.", _S({"name": STR, "new_name": STR}, ["name", "new_name"]), rename_feature),
    ("delete_feature", "Delete a feature/sketch/plane by name.", _S({"name": STR}, ["name"]), delete_feature),
    ("undo", "Undo the last step(s) in SolidWorks.", _S({"steps": NUM}), undo),
    ("rebuild", "Force a full rebuild of the active document.", _S({}), rebuild),
]

HANDLERS = {t[0]: t[3] for t in TOOLS}


def tool_defs():
    return [Tool(name=n, description=desc, inputSchema=schema) for n, desc, schema, _ in TOOLS]


def handle(name, arguments, automation, format_result):
    try:
        pythoncom.CoInitialize()
        r = HANDLERS[name](Ctx(automation), arguments or {})
        if isinstance(r, list):
            return r
        return [TextContent(type="text", text=format_result(r))]
    except Exception as e:
        logger.error(f"{name} failed: {e}\n{traceback.format_exc()}")
        return [TextContent(type="text", text=f"[ERROR] {name}: {e}")]
