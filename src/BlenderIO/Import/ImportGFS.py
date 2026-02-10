from .ImportTextures import import_textures
from .ImportMaterials import import_materials
from . import ImportModel
from .ImportAnimations import create_rest_pose, import_animations
from .ImportPhysics import import_physics
from .Import0x000100F8 import import_0x000100F8
from . import ImportEPLs


def import_gfs_object(gfs, raw_gfs, share_textures, external_textures, name, errorlog, import_policies):
    # Override default builtin images with external textures
    has_external_tex = len(external_textures.textures) > 0
    if has_external_tex:
        external_tex_names = {t.name: i for i, t in enumerate(external_textures.textures)}
        for tex in gfs.textures:
            if tex.name not in external_tex_names:
                continue
            etex = external_textures.textures[external_tex_names[tex.name]]
            tex.image_data = etex.image_data
    
    # external_tex = import_textures(external_textures, share_textures)
    textures     = import_textures(gfs, share_textures)
    # for nm, tx in external_tex.items():
    #     if nm in textures:
    #         img = textures[nm]
    #         tx.GFSTOOLS_ImageProperties.unknown_1 = img.GFSTOOLS_ImageProperties.unknown_1
    #         tx.GFSTOOLS_ImageProperties.unknown_2 = img.GFSTOOLS_ImageProperties.unknown_2
    #         tx.GFSTOOLS_ImageProperties.unknown_3 = img.GFSTOOLS_ImageProperties.unknown_3
    #         tx.GFSTOOLS_ImageProperties.unknown_4 = img.GFSTOOLS_ImageProperties.unknown_4
    #         textures[nm] = tx
    
    materials = import_materials(gfs, textures, errorlog)
    armature, gfs_to_bpy_bone_map = ImportModel.import_model(gfs, name, materials, errorlog, import_policies.merge_vertices, import_policies.bone_pose, raw_gfs, import_policies)
    
    create_rest_pose(gfs, armature, gfs_to_bpy_bone_map)
    import_animations(gfs, armature, name, is_external=False, gfs_to_bpy_bone_map=gfs_to_bpy_bone_map, import_policies=import_policies)
    
    import_physics(gfs, armature)
    import_0x000100F8(gfs, armature)
    
    ImportEPLs.import_epls(gfs, armature, gfs_to_bpy_bone_map, errorlog, import_policies)

    mprops = armature.data.GFSTOOLS_ModelProperties
    mprops.has_external_tex = has_external_tex
    for nm, tx in textures.items():
        if (tx.users == 0):
            btx = armature.GFSTOOLS_ModelProperties.unused_textures.add()
            btx.name    = nm
            btx.texture = tx
            

    return armature
