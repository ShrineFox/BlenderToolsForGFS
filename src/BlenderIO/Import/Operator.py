import os

import bpy
from mathutils import Matrix, Quaternion
from bpy_extras.io_utils import ImportHelper

from ...FileFormats.GFS import GFSInterface, UnsupportedVersionError, NotAGFSFileError
from ...FileFormats.TexBin import TexBinBinary
from ...FileFormats.GFS.Interface import EPLFileInterface
from ..Data import bone_pose_enum_options
from ..Data import anim_boundbox_policy_options
from ..Preferences import get_preferences
from ..Utils.Object import lock_obj_transforms
from ..modelUtilsTest.API.Operator import get_op_idname
from ..Globals import ErrorLogger
from .ImportGFS import import_gfs_object
from .ImportEPLs import import_epl
from .ImportAnimations import import_animations


def set_fps(self, context):
    if self.policies.set_fps:
        context.scene.render.fps = 30


def set_clip(self, context):
    if self.policies.set_clip:
        for a in context.screen.areas:
            if a.type == 'VIEW_3D':
                for s in a.spaces:
                    if s.type == 'VIEW_3D':
                        s.clip_end = 1000000


def define_set_fps():
    return bpy.props.BoolProperty(
        name="Set Blender Scene FPS to 30",
        description="Set the animation framerate of the current scene to 30, so that imported animations display at the correct speed",
        default=True
    )


def define_align_quats():
    return bpy.props.BoolProperty(name="Align Animation Quaternions",
                                     description="Eliminate sign flips between animation quaternions",
                                     default=False)


class ImportPolicies(bpy.types.PropertyGroup):
    align_quats: define_align_quats()
    set_fps:     define_set_fps()
    
    set_clip: bpy.props.BoolProperty(name="Set P5R Screen Clip",
                                     description="Set Blender's maximum render distance to 1000000, so that most Persona 5 model rendering is not culled",
                                     default=True)
    
    merge_vertices: bpy.props.BoolProperty(
        name="Merge Vertices",
        description="Merge vertices with the same position data such that "\
                    "they form a smooth mesh that Blender can more accurately "\
                    "calculate normal and tangent vectors for. Merged GFS "\
                    "vertices become loops of the Blender vertices. Note that "\
                    "any vertices that are not part of faces are currently "
                    "dropped by this feature",
        default=True
    )
     
    connect_child_bones: bpy.props.BoolProperty(
        name="Connect Child Bones",
        description="Attach bone tails and heads if they are sufficiently close",
        default=False)
        
    bone_pose: bpy.props.EnumProperty(
        name="Bind Pose",
        description="The method used to construct a bind pose for the skeleton",
        items=bone_pose_enum_options()

    )
    
    anim_boundbox_policy: bpy.props.EnumProperty(
        name="Animation Bounding Boxes",
        description="The default setting for how to calculate animation bounding boxes when exporting the model",
        items=anim_boundbox_policy_options()
    )
    
    epl_tests: bpy.props.BoolProperty(
        name="EPL Tests",
        description="Imports embedded EPL models as Blender Data",
        default=False
    )


class ImportGFS(bpy.types.Operator, ImportHelper):
    bl_idname = 'import_file.import_gfs'
    bl_label = 'Persona 5 Royal - PC (.GMD, .GFS)'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".GMD"
    internal_idname = ''

    debug_mode: bpy.props.BoolProperty(
                                           default=False,
                                           options={'HIDDEN'},
                                      )

    filter_glob: bpy.props.StringProperty(
                                              default='*.GMD;*.GFS',
                                              options={'HIDDEN'},
                                          )
    
    policies: bpy.props.PointerProperty(type=ImportPolicies)
    
    def invoke(self, context, event):
        prefs = get_preferences()
        self.policies.align_quats          = prefs.align_quats
        self.policies.set_fps              = prefs.set_fps
        self.policies.set_clip             = prefs.set_clip
        self.policies.merge_vertices       = prefs.merge_vertices
        self.policies.bone_pose            = prefs.bone_pose
        self.policies.connect_child_bones  = prefs.connect_child_bones
        self.policies.anim_boundbox_policy = prefs.anim_boundbox_policy
        return super().invoke(context, event)
    
    def draw(self, context):
        pass

    def load_external_textures(self, filepath):
        filename = os.path.splitext(os.path.split(filepath)[1])[0]
    
        class TexHolder:
            def __init__(self):
                self.textures = []
            
        external_textures = TexHolder()
        
        # TODO: LOADING TEXTURES CAUSES HUGE RAM SPIKE???
        # One thing to note: it's better to simply keep a list of which textures
        # exist in the bin rather than loading the entire thing up immediately...?
        # return external_textures
        file_dir = os.path.split(filepath)[0]
        for dname in os.listdir(file_dir):
            if dname.upper() == "TEXTURES" and os.path.isdir(os.path.join(file_dir, dname)):
                bits = filename.split("_")
                p1 = int(bits[0][-3:])
                p2 = int(bits[1])
                p3 = int(bits[2])
                
                
                tex_fn = f"TEX{p1:0>3}_{p2:0>3}_{p3:0>2}_{{0:0>2}}.BIN"
                
                if os.path.exists(os.path.join(file_dir, dname, tex_fn.format("00"))):
                    tex_fp = os.path.join(file_dir, dname, tex_fn.format("00"))
                    tex_ft = os.path.join(os.path.split(filepath)[0], dname, tex_fn)
                elif os.path.exists(os.path.join(file_dir, dname, tex_fn.lower().format("00"))):
                    tex_fp = os.path.join(file_dir, dname, tex_fn.lower().format("00"))
                    tex_ft = os.path.join(os.path.split(filepath)[0], dname, tex_fn.lower())
                else:
                    continue
            
                class TexWrapper:
                    def __init__(self, name, data):
                        self.name = name
                        self.image_data = data
                        
                        self.unknown_1 = 0
                        self.unknown_2 = 0
                        self.unknown_3 = 0
                        self.unknown_4 = 0
                
                tb = TexBinBinary()
                tb.read(tex_fp, tex_ft)
                for tex in tb.textures:
                    external_textures.textures.append(TexWrapper(tex.name.rstrip(b'\x00').decode('sjis'), tex.payload))
                break
        return external_textures

    def safe_gfs_load(self, filepath, share_textures, external_textures, errorlog):
        warnings = []
        try:
            with open(filepath, 'rb') as F:
                raw_gfs = F.read()
            gfs = GFSInterface.from_bytes(raw_gfs, warnings=warnings)
        except NotAGFSFileError as e:
            errorlog.log_error_message(str(e))
        except UnsupportedVersionError as e:
            errorlog.log_error_message(f"The file you attempted to load is an unsupported version: {str(e)}.")

        # Add any file-loading warnings to the warnings list
        for warning_msg in warnings:
            errorlog.log_warning_message(warning_msg)
        
        # Report any file-loading errors
        if len(errorlog.errors):
            errorlog.digest_errors(self.debug_mode)
            return None

        # Now import file data to Blender
        filename = os.path.splitext(os.path.split(filepath)[1])[0]
        armature = import_gfs_object(gfs, raw_gfs, share_textures, external_textures, filename, errorlog, self.policies)
        
        return gfs, armature

    @ErrorLogger.display_exceptions("The file you are trying to import.")
    def import_file(self, context, filepath):
        if bpy.context.view_layer.objects.active is not None:        
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action='DESELECT')
        
        # Try to load file and log any errors...
        errorlog = ErrorLogger()

        external_textures = self.load_external_textures(filepath)
        gfs, armature = self.safe_gfs_load(filepath, False, external_textures, errorlog)
        if gfs is None:
            return {'CANCELLED'}
        
        for bone in armature.data.bones:
            subobjs = []
            cur_obj = None
            for prop in bone.GFSTOOLS_NodeProperties.properties:
                if prop.dname == "fldLayoutOfModel_major" and prop.dtype == "INT32":
                    cur_obj = prop.int32_data
                elif prop.dname == "fldLayoutOfModel_minor" and prop.dtype == "INT32" and cur_obj is not None:
                    subobjs.append((cur_obj, prop.int32_data))
                    cur_obj = None
            
            for major, minor in subobjs:
                sub_fn = f"M{major:0>3}_{minor:0>3}.GMD"
                new_fp = os.path.join(os.path.split(filepath)[0], "OBJECT", sub_fn)
                if os.path.exists(new_fp):
                    subgfs, subarmature = self.safe_gfs_load(new_fp, True, external_textures, errorlog)
                    
                    lock_obj_transforms(subarmature)
                    constraint = subarmature.constraints.new("CHILD_OF")
                    constraint.target    = armature
                    constraint.subtarget = bone.name
                    constraint.inverse_matrix = Matrix.Identity(4)
                    
                    transform = Quaternion([.5**.5, 0., 0., .5**.5]).to_matrix().to_4x4()
                    subarmature.matrix_local = transform
                else:
                    errorlog.log_warning_message(f"Sub-object {sub_fn} was not found at {new_fp}")
            
        
        set_fps(self, context)
        set_clip(self, context)
        
        # Report any warnings that were logged
        if len(errorlog.warnings):
            errorlog.digest_warnings(self.debug_mode)
            self.report({"INFO"}, "Import successful, with warnings.")
        elif not self.debug_mode:
            self.report({"INFO"}, "Import successful.")
            
        return {'FINISHED'}

    
    def execute(self, context):
        return self.import_file(context, self.filepath)

    @classmethod
    def register(cls):
        cls.internal_idname = get_op_idname(cls)
        
        bpy.utils.register_class(CUSTOM_PT_GFSModelImportSettings)
        bpy.utils.register_class(CUSTOM_PT_GFSModelDeveloperImportSettings)
        
    @classmethod
    def unregister(cls):
        bpy.utils.unregister_class(CUSTOM_PT_GFSModelImportSettings)
        bpy.utils.unregister_class(CUSTOM_PT_GFSModelDeveloperImportSettings)


class ImportGAP(bpy.types.Operator, ImportHelper):
    bl_idname = 'import_file.import_gap'
    bl_label = 'Persona 5 Royal - PC (.GAP)'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = "*.GAP"
    internal_idname = ''

    def fetch_armatures(self, context):
        armature_list = []
        for obj in bpy.data.objects:
            if obj.type == "ARMATURE":
                armature_list.append((obj.name, obj.name, obj.name, "OUTLINER_OB_ARMATURE", len(armature_list)))
        return tuple(armature_list)
    
    armature_name: bpy.props.EnumProperty(items=fetch_armatures,
                                     name="Armature")
    
    filter_glob: bpy.props.StringProperty(
                                              default="*.GAP",
                                              options={'HIDDEN'},
                                          )

    debug_mode: bpy.props.BoolProperty(
                                           default=False,
                                           options={'HIDDEN'},
                                      )
    
    policies: bpy.props.PointerProperty(type=ImportPolicies)
    
    def invoke(self, context, event):
        prefs = get_preferences()
        self.policies.align_quats          = prefs.align_quats
        self.policies.set_fps              = prefs.set_fps
        self.policies.set_clip             = prefs.set_clip
        self.policies.anim_boundbox_policy = prefs.anim_boundbox_policy
        return super().invoke(context, event)
    
    def draw(self, context):
        pass

    def find_selected_model(self, context):
        sel_obj = context.active_object
        if sel_obj is None:
            return None
        while sel_obj.parent is not None:
            sel_obj = sel_obj.parent
        if sel_obj.type == "ARMATURE":
            return sel_obj
        return None

    @ErrorLogger.display_exceptions("The file you are trying to import.")
    def import_file(self, context, armature, filepath):
        if bpy.context.view_layer.objects.active is not None:        
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action='DESELECT')

        # Try to load file and log any errors...
        errorlog = ErrorLogger()            
        if self.armature_name is None:
            errorlog.log_error_message("No armatures exist in the scene. Animations cannot be imported")
        
        # Report an error if there's no armature
        if len(errorlog.errors):
            errorlog.digest_errors(self.debug_mode)
            return {'CANCELLED'}
        
        warnings = []
        try:
            gfs = GFSInterface.from_file(filepath, warnings=warnings)
        except NotAGFSFileError as e:
            errorlog.log_error_message(str(e))
        except UnsupportedVersionError as e:
            errorlog.log_error_message(f"The file you attempted to load is an unsupported version: {str(e)}.")

        # Add any file-loading warnings to the warnings list
        for warning_msg in warnings:
            errorlog.log_warning_message(warning_msg)

        # Report any file-loading errors
        if len(errorlog.errors):
            errorlog.digest_errors(self.debug_mode)
            return {'ERROR'}
        
        # Now import file data to Blender
        filename = os.path.splitext(os.path.split(filepath)[1])[0]
        import_animations(gfs, armature, filename, is_external=True, import_policies=self.policies)
        
        # Report any warnings that were logged
        errorlog.digest_warnings(self.debug_mode)
        
        set_fps(self, context)
        set_clip(self, context)
        
        if len(errorlog.warnings):
            errorlog.digest_warnings(self.debug_mode)
            self.report({"INFO"}, "Import successful, with warnings.")
        elif not self.debug_mode:
            self.report({"INFO"}, "Import successful.")
        
        return {'FINISHED'}
    
    def execute(self, context):
        return self.import_file(context, bpy.data.objects[self.armature_name], self.filepath)

    @classmethod
    def register(cls):
        cls.internal_idname = get_op_idname(cls)
        
        bpy.utils.register_class(CUSTOM_PT_GFSAnimImportSettings)
        
    @classmethod
    def unregister(cls):
        bpy.utils.unregister_class(CUSTOM_PT_GFSAnimImportSettings)


class CUSTOM_PT_GFSModelImportSettings(bpy.types.Panel):
    """
    Adapted from https://blender.stackexchange.com/a/217796
    """
    
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'TOOL_PROPS'
    bl_label = "Import Settings"
    bl_options = set()

    @classmethod
    def poll(cls, context):
        sfile = context.space_data
        operator = sfile.active_operator
        return operator.bl_idname == ImportGFS.internal_idname

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False  # No animation.

        sfile = context.space_data
        operator = sfile.active_operator
        policies = operator.policies

        layout.prop(policies, 'align_quats')
        layout.prop(policies, 'anim_boundbox_policy')
        layout.prop(policies, 'set_fps')
        layout.prop(policies, 'set_clip')
        layout.prop(policies, 'merge_vertices')
        layout.prop(policies, 'bone_pose')
        layout.prop(policies, 'connect_child_bones')


class CUSTOM_PT_GFSModelDeveloperImportSettings(bpy.types.Panel):
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'TOOL_PROPS'
    bl_label = "Developer Settings"
    bl_options = set()

    @classmethod
    def poll(cls, context):
        sfile = context.space_data
        operator = sfile.active_operator
        prefs = get_preferences()
        return operator.bl_idname == ImportGFS.internal_idname and prefs.developer_mode

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False  # No animation.

        sfile = context.space_data
        operator = sfile.active_operator
        policies = operator.policies

        layout.prop(policies, 'epl_tests')
        layout.prop(operator, 'debug_mode')


class CUSTOM_PT_GFSAnimImportSettings(bpy.types.Panel):
    """
    Adapted from https://blender.stackexchange.com/a/217796
    """
    
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'TOOL_PROPS'
    bl_label = "Import Settings"
    bl_options = set()

    @classmethod
    def poll(cls, context):
        sfile = context.space_data
        operator = sfile.active_operator
        return operator.bl_idname == ImportGAP.internal_idname

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False  # No animation.

        sfile = context.space_data
        operator = sfile.active_operator
        policies = operator.policies

        layout.prop(operator, 'armature_name')
        layout.prop(policies, 'align_quats')
        layout.prop(policies, 'anim_boundbox_policy')
        layout.prop(policies, 'set_fps')
        layout.prop(policies, 'set_clip')


class ImportEPL(bpy.types.Operator, ImportHelper):
    bl_idname = 'import_file.import_epl'
    bl_label = 'Persona 5 Royal - PC (.EPL)'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".EPL"
    internal_idname = ''

    debug_mode: bpy.props.BoolProperty(
                                           default=False,
                                           options={'HIDDEN'},
                                      )

    filter_glob: bpy.props.StringProperty(
                                              default='*.EPL',
                                              options={'HIDDEN'},
                                          )
    
    policies: bpy.props.PointerProperty(type=ImportPolicies)
    
    @classmethod
    def poll(cls, context):
        prefs = get_preferences()
        return prefs.developer_mode
    
    def invoke(self, context, event):
        prefs = get_preferences()
        self.policies.align_quats          = prefs.align_quats
        self.policies.set_fps              = prefs.set_fps
        self.policies.set_clip             = prefs.set_clip
        self.policies.merge_vertices       = prefs.merge_vertices
        self.policies.bone_pose            = prefs.bone_pose
        self.policies.connect_child_bones  = prefs.connect_child_bones
        self.policies.anim_boundbox_policy = prefs.anim_boundbox_policy
        return super().invoke(context, event)
    
    def draw(self, context):
        pass

    @ErrorLogger.display_exceptions("The file you are trying to import.")
    def import_file(self, context, filepath):
        if bpy.context.view_layer.objects.active is not None:        
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action='DESELECT')
        
        # Try to load file and log any errors...
        errorlog = ErrorLogger()
        try:
            epl = EPLFileInterface.from_file(filepath)
        except NotAGFSFileError as e:
            errorlog.log_error_message(str(e))
        except UnsupportedVersionError as e:
            errorlog.log_error_message(f"The file you attempted to load is an unsupported version: {str(e)}.")


        # Now import file data to Blender
        filename = os.path.splitext(os.path.split(filepath)[1])[0]
        import_epl(filename, epl, errorlog, self.policies)
        
        set_fps(self, context)
        set_clip(self, context)
        
        # Report any warnings that were logged
        if len(errorlog.warnings):
            errorlog.digest_warnings(self.debug_mode)
            self.report({"INFO"}, "Import successful, with warnings.")
        elif not self.debug_mode:
            self.report({"INFO"}, "Import successful.")
            
        return {'FINISHED'}
    
    def execute(self, context):
        return self.import_file(context, self.filepath)

    @classmethod
    def register(cls):
        cls.internal_idname = get_op_idname(cls)
        bpy.utils.register_class(CUSTOM_PT_EPLImportSettings)
        
    @classmethod
    def unregister(cls):
        bpy.utils.unregister_class(CUSTOM_PT_EPLImportSettings)


class CUSTOM_PT_EPLImportSettings(bpy.types.Panel):
    """
    Adapted from https://blender.stackexchange.com/a/217796
    """
    
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'TOOL_PROPS'
    bl_label = "Import Settings"
    bl_options = set()

    @classmethod
    def poll(cls, context):
        sfile = context.space_data
        operator = sfile.active_operator
        return operator.bl_idname == ImportEPL.internal_idname

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False  # No animation.

        sfile = context.space_data
        operator = sfile.active_operator
        policies = operator.policies

        layout.prop(policies, 'align_quats')
        layout.prop(policies, 'anim_boundbox_policy')
        layout.prop(policies, 'set_fps')
        layout.prop(policies, 'set_clip')
        layout.prop(policies, 'merge_vertices')
        layout.prop(policies, 'bone_pose')
        layout.prop(policies, 'connect_child_bones')