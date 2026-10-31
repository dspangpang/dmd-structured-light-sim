import bpy

SIM_TAG_KEY = "sim_tag"
TAGS = {"projector_root", "projector_spot"}

def delete_all_objects():
    # delete all objects in scene
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=True)

    # purge orphan datablocks (simple)
    for coll in (bpy.data.meshes, bpy.data.cameras, bpy.data.lights, bpy.data.materials, bpy.data.images):
        for db in list(coll):
            if db.users == 0:
                coll.remove(db)

def delete_tagged_projector_objects(verbose=True):
    # delete only objects tagged by us
    names = []
    for o in bpy.data.objects:
        try:
            if o.get(SIM_TAG_KEY) in TAGS:
                names.append(o.name)
        except:
            pass

    if verbose:
        print("[Cleanup] tagged projector objects:", names)

    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception as e:
            if verbose:
                print("[Cleanup] remove failed:", name, e)