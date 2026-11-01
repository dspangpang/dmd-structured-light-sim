"""Small explicit fixtures for consumers of the core-sim calibration schema."""
import json

def write_calibration(path, size, cameras, projector=None):
    def matrix(rows):
        return {'type_id':'opencv-matrix','rows':len(rows),'cols':len(rows[0]),'dt':'d','data':[x for row in rows for x in row]}
    devices={}
    for name,(k,rt) in cameras.items():
        devices[name]={'kind':'camera','image_size':list(size),'K':matrix(k),'world_to_device':matrix(rt)}
    if projector:
        psize,k,rt=projector
        devices['Projector']={'kind':'projector','image_size':list(psize),'K':matrix(k),'world_to_device':matrix(rt)}
    value={'schema':'dmd-sim.calibration.v1','pixel_coordinate_contract':'opencv_integer_centers_v1','translation_unit':'meter','reference_camera':next(iter(cameras)),'devices':devices}
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value))
