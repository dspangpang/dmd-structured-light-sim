"""Run with Blender --background --factory-startup --python this_file.

Tests real Blender projections and Cycles raster/texture sampling. No external
assets or GPU are needed; all rendered artifacts live in a temporary directory.
"""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Matrix, Vector
from bpy_extras.object_utils import world_to_camera_view

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from scene import add_camera
from render import validate_blender_camera_intrinsics
from projector import add_projector, update_projector_image, validate_projector_projection_state
from pixel_coordinates import projector_lens_shift


def make_camera(width, height, cx=None, cy=None, center=(0, 0, 0), rotation=None):
    cx = (width - 1) / 2 if cx is None else cx
    cy = (height - 1) / 2 if cy is None else cy
    f = float(width)
    K = [[f, 0., cx], [0., f, cy], [0., 0., 1.]]
    rotation = Matrix.Identity(3) if rotation is None else rotation
    cfg = SimpleNamespace(RES_X=width, RES_Y=height, f_mm=10.,
        sensor_w=10., sensor_h=10.*height/width,
        CAMERA_INTRINSIC_MATRIX_PX=K, CAMERA_NEAR_CLIP=.001)
    cam = add_camera(cfg, "audit_camera", center, rotation_matrix=rotation, intrinsic_matrix=K)
    device_to_world = np.asarray(rotation) @ np.diag([1., -1., -1.])
    R = device_to_world.T
    t = -R @ np.asarray(center)
    P = np.asarray(K) @ np.column_stack((R, t * 1000.))
    cfg.APP_CAMERA_DEVICES = [{"name":cam.name,"role":"audit", "intrinsic_matrix_px":K,
        "device_to_world_3x3":device_to_world.tolist(),"center_world_m":list(center),
        "projection_matrix_px_3x4":P.tolist()}]
    sc = bpy.context.scene
    sc.render.resolution_x, sc.render.resolution_y = width, height
    sc.render.resolution_percentage=100
    sc.render.pixel_aspect_x=sc.render.pixel_aspect_y=1
    sc.camera=cam
    bpy.context.view_layer.update()
    return cam,cfg


def projection_checks():
    errors=[]
    for width,height in [(640,480),(480,640),(641,479)]:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        cameras=[]; devices=[]
        for shift in [(0.,0.),(7.25,-4.125)]:
            rot=Matrix.Rotation(.21,3,'Y') @ Matrix.Rotation(-.13,3,'Z')
            cam,cfg=make_camera(width,height,(width-1)/2+shift[0],(height-1)/2+shift[1],(.1,-.2,.3),rot)
            cameras.append(cam); devices.extend(cfg.APP_CAMERA_DEVICES)
            K=np.asarray(cfg.CAMERA_INTRINSIC_MATRIX_PX)
            for u,v in [(0,0),(width-1,height-1),((width-1)/2,(height-1)/2),(23.25,47.75)]:
                local=Vector(((u-K[0,2])/K[0,0],-(v-K[1,2])/K[1,1],-1.))
                ndc=world_to_camera_view(bpy.context.scene,cam,cam.matrix_world@local)
                actual=np.array([ndc.x*width-.5,(1-ndc.y)*height-.5])
                errors.append(float(np.max(np.abs(actual-[u,v]))))
        cfg.APP_CAMERA_DEVICES=devices
        validate_blender_camera_intrinsics(cameras,cfg)
        # A half-pixel regression must be detected by the production validator.
        cameras[0].data.shift_x += .5/max(width,height)
        try:
            validate_blender_camera_intrinsics(cameras,cfg)
        except ValueError:
            pass
        else:
            raise AssertionError('camera validator accepted a half-pixel error')
    assert max(errors)<.002,errors
    return max(errors)


def setup_cycles():
    sc=bpy.context.scene
    sc.render.engine='CYCLES'; sc.cycles.device='CPU'; sc.cycles.samples=512
    sc.cycles.use_denoising=False; sc.cycles.use_adaptive_sampling=False
    sc.cycles.pixel_filter_type='BOX'; sc.cycles.filter_width=1.
    sc.cycles.max_bounces=1
    sc.render.threads_mode='FIXED'; sc.render.threads=2
    sc.render.image_settings.file_format='OPEN_EXR'; sc.render.image_settings.color_depth='32'
    sc.render.image_settings.color_mode='RGBA'
    sc.world=bpy.data.worlds.new('black'); sc.world.use_nodes=True
    sc.world.node_tree.nodes['Background'].inputs['Strength'].default_value=0


def render_image(path):
    sc=bpy.context.scene; sc.render.filepath=str(path)
    bpy.ops.render.render(write_still=True)
    img=bpy.data.images.load(str(path),check_existing=False)
    w,h=img.size; data=np.empty(w*h*4,dtype=np.float32)
    img.pixels.foreach_get(data); bpy.data.images.remove(img)
    return data.reshape(h,w,4)[::-1,:,:3].copy()


def plane():
    bpy.ops.mesh.primitive_plane_add(size=4,location=(0,0,-1))
    obj=bpy.context.object
    mat=bpy.data.materials.new('audit_material'); mat.use_nodes=True
    obj.data.materials.append(mat)
    return mat


def rendered_camera_check(tmp):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    w,h=64,48;cam,cfg=make_camera(w,h,30.25,25.125)
    setup_cycles(); mat=plane(); nodes=mat.node_tree.nodes; nodes.clear(); links=mat.node_tree.links
    pos=nodes.new('ShaderNodeNewGeometry'); sep=nodes.new('ShaderNodeSeparateXYZ')
    links.new(pos.outputs['Position'],sep.inputs[0])
    rgb=nodes.new('ShaderNodeCombineXYZ')
    K=cfg.CAMERA_INTRINSIC_MATRIX_PX
    for axis,scale,offset in [(0,K[0][0]/w,K[0][2]/w),(1,-K[1][1]/h,K[1][2]/h)]:
        mul=nodes.new('ShaderNodeMath');mul.operation='MULTIPLY_ADD'
        links.new(sep.outputs[axis],mul.inputs[0]);mul.inputs[1].default_value=scale;mul.inputs[2].default_value=offset
        links.new(mul.outputs[0],rgb.inputs[axis])
    em=nodes.new('ShaderNodeEmission');out=nodes.new('ShaderNodeOutputMaterial')
    links.new(rgb.outputs[0],em.inputs[0]);links.new(em.outputs[0],out.inputs['Surface'])
    image=render_image(tmp/'camera.exr')
    yy,xx=np.indices((h,w)); err=np.stack([image[:,:,0]*w-xx,image[:,:,1]*h-yy],axis=-1)
    maximum=float(np.max(np.abs(err[3:-3,3:-3])))
    assert maximum<.08,('rendered camera pixel error',maximum)
    return maximum


def texture_file(tmp,name,w,h,ramp):
    img=bpy.data.images.new(name,width=w,height=h,float_buffer=True)
    img.colorspace_settings.name='Non-Color'
    yy,xx=np.indices((h,w)); pixels=np.ones((h,w,4),dtype=np.float32)
    if ramp:
        pixels[:,:,0]=xx/w; pixels[:,:,1]=(h-1-yy)/h
    img.pixels.foreach_set(pixels.ravel()); img.filepath_raw=str(tmp/(name+'.exr'));img.file_format='OPEN_EXR';img.save()
    path=img.filepath_raw;bpy.data.images.remove(img);return path


def rendered_projector_check(tmp, w=64, h=48):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    cam,cfg=make_camera(w,h,(w-1)/2+1.25,(h-1)/2-.75);setup_cycles();mat=plane()
    bsdf=mat.node_tree.nodes.get('Principled BSDF');bsdf.inputs['Base Color'].default_value=(1,1,1,1)
    bsdf.inputs['Roughness'].default_value=1.;bsdf.inputs['Specular IOR Level'].default_value=0.
    white=texture_file(tmp,'white',w,h,False);ramp=texture_file(tmp,'ramp',w,h,True)
    K=cfg.CAMERA_INTRINSIC_MATRIX_PX
    sx,sy=projector_lens_shift(K[0][2],K[1][2],w,h)
    pcfg=SimpleNamespace(PROJECTOR_THROW_RATIO=1.,PROJECTOR_LENS_SHIFT_X=sx,PROJECTOR_LENS_SHIFT_Y=sy,
        PROJECTOR_ENERGY=100.,PROJECTOR_IMAGE=white,PROJECTOR_RES_X=w,PROJECTOR_RES_Y=h,
        PROJECTOR_INTRINSIC_MATRIX_PX=K,PROJECTOR_TEXTURE_INTERPOLATION='Linear')
    proj=add_projector(pcfg);proj.matrix_world=Matrix.Identity(4)
    bpy.context.view_layer.update()
    validate_projector_projection_state(proj,white)
    a=render_image(tmp/'white_render.exr')
    update_projector_image(proj,ramp);validate_projector_projection_state(proj,ramp)
    b=render_image(tmp/'ramp_render.exr')
    yy,xx=np.indices((h,w));ratio=b/np.maximum(a,1e-20)
    err=np.stack([ratio[:,:,0]*w-xx,ratio[:,:,1]*h-yy],axis=-1)
    maximum=float(np.max(np.abs(err[5:-5,5:-5])))
    assert maximum<.08,('rendered projector pixel error',maximum)
    # Changing the actual Mapping location must fail even if scale is unchanged.
    spot=next(c for c in proj.children if c.type=='LIGHT')
    mapping=spot.data.node_tree.nodes['Group'].node_tree.nodes['Mapping.001']
    mapping.inputs['Location'].default_value[0] += .5/w
    try:
        validate_projector_projection_state(proj,ramp)
    except RuntimeError:
        pass
    else:
        raise AssertionError('projector validator accepted a half-pixel error')
    return maximum


def main():
    with tempfile.TemporaryDirectory(prefix='blender-pixel-contract-') as tmp:
        result={'projection_max_error_px':projection_checks(),
                'cycles_camera_max_error_px':rendered_camera_check(Path(tmp)),
                'cycles_projector_max_error_px':rendered_projector_check(Path(tmp)),
                'cycles_portrait_projector_max_error_px':rendered_projector_check(Path(tmp),48,64)}
    print('PIXEL_COORDINATE_CHECK_PASSED '+json.dumps(result,sort_keys=True))


if __name__=='__main__':
    main()
