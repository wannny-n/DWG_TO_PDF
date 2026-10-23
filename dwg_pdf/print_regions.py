"""Рамки без имён блоков и сохранённое окно печати модели."""
from math import hypot


def paper_dimensions(width, height, sizes, preferred_scale=1):
    a, b = sorted((width, height))
    # Check actual dimensions at conventional CAD scales, not aspect ratio
    # alone: an arbitrary rectangle must still require a saved print window.
    candidates = []
    for scale in (1, 2, 2.5, 4, 5, 10, 20, 25, 40, 50, 100, 200, 250, 400, 500, 1000):
        for name, (pa, pb) in sizes.items():
            error = max(abs(a/(scale*pa)-1), abs(b/(scale*pb)-1))
            if error <= .025:
                priority = 0 if scale == 1 else 1 if scale == preferred_scale else 2
                candidates.append((priority, error, scale, name, pa, pb))
    if candidates:
        _, _, _, name, pa, pb = min(candidates)
        return name, (pb, pa) if width > height else (pa, pb)
    return None


def loose_rectangles(model):
    """Собирает прямоугольники из четырёх отдельных линий, не трогая DWG."""
    horizontal, vertical = [], []
    for entity in model.query('LINE'):
        a, b = entity.dxf.start, entity.dxf.end
        if abs(a.y-b.y) < .02 and abs(a.x-b.x) > 100:
            horizontal.append((min(a.x,b.x), max(a.x,b.x), (a.y+b.y)/2))
        elif abs(a.x-b.x) < .02 and abs(a.y-b.y) > 100:
            vertical.append(((a.x+b.x)/2, min(a.y,b.y), max(a.y,b.y)))
    for left, right, bottom in horizontal:
        for x, low, top in vertical:
            if abs(x-left) > .02 or abs(low-bottom) > .02:
                continue
            if (any(abs(xx-right)<.02 and abs(lo-bottom)<.02 and abs(hi-top)<.02 for xx,lo,hi in vertical)
                    and any(abs(a-left)<.02 and abs(b-right)<.02 and abs(y-top)<.02 for a,b,y in horizontal)):
                yield (left,bottom,right,top), 'Рамка из линий'


def polyline_regions(model):
    for entity in model.query('LWPOLYLINE POLYLINE'):
        points = list(entity.get_points('xy')) if entity.dxftype() == 'LWPOLYLINE' else list(entity.points())
        if len(points) not in (4,5):
            continue
        if not entity.is_closed and hypot(points[0][0]-points[-1][0],points[0][1]-points[-1][1]) > .02:
            continue
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        box = (min(xs),min(ys),max(xs),max(ys))
        if box[2]-box[0] <= 1 or box[3]-box[1] <= 1:
            continue
        if all(min(abs(p[0]-box[0]),abs(p[0]-box[2])) < .02 and
               min(abs(p[1]-box[1]),abs(p[1]-box[3])) < .02 for p in points):
            yield box, entity.dxf.layer


def saved_model_window(document):
    from ezdxf.math import BoundingBox2d, Vec3, Matrix44
    model = document.modelspace()
    if model.dxf.plot_type != 4:
        return None
    x1,y1,x2,y2 = (model.dxf.get(k,0) for k in ('plot_window_x1','plot_window_y1','plot_window_x2','plot_window_y2'))
    if x2 <= x1 or y2 <= y1:
        return None
    viewport = document.viewports.get_config('*Active')[0]
    # The saved window is in DCS, relative to the active view target.
    # Ordinary top views keep WCS axes; the twist rotates DCS about the target.
    from math import radians
    transform = Matrix44.z_rotate(-float(viewport.dxf.view_twist))
    transform @= Matrix44.translate(*viewport.dxf.target)
    box = BoundingBox2d(transform.transform_vertices([(x1,y1,0),(x2,y1,0),(x2,y2,0),(x1,y2,0)]))
    width,height = float(model.dxf.paper_width),float(model.dxf.paper_height)
    if int(model.dxf.plot_rotation)%2:
        width,height = height,width
    if width <= 0 or height <= 0:
        width,height = x2-x1,y2-y1
    return box,width,height
