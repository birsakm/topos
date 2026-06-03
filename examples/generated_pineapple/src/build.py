import math

import bpy
from mathutils import Vector


def _clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _mat(name, color, roughness=0.7):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = roughness
    return mat


def _assign(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    obj.color = mat.diffuse_color


def _add_body(mat):
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=96,
        ring_count=48,
        location=(0, 0, 0.58),
    )
    obj = bpy.context.active_object
    obj.name = "PineappleBody"
    obj.scale = (0.42, 0.42, 0.72)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    _assign(obj, mat)
    return obj


def _diamond_mesh(name, center, normal, tangent, width, height, mat):
    normal = normal.normalized()
    tangent = tangent.normalized()
    bitangent = normal.cross(tangent).normalized()
    center = Vector(center) + normal * 0.008
    verts = [
        center + bitangent * (height * 0.5),
        center + tangent * (width * 0.5),
        center - bitangent * (height * 0.5),
        center - tangent * (width * 0.5),
    ]
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata([tuple(v) for v in verts], [], [(0, 1, 2, 3)])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    _assign(obj, mat)
    return obj


def _add_pattern(body_mat, scale_mat, thorn_mat):
    rings = 15
    for ring in range(rings):
        v = -0.82 + ring * (1.64 / (rings - 1))
        radius = 0.42 * math.sqrt(max(0.08, 1.0 - v * v))
        z = 0.58 + 0.72 * v
        count = max(10, int(18 * radius / 0.42))
        offset = (ring % 2) * math.pi / count
        for i in range(count):
            theta = 2 * math.pi * i / count + offset
            x = radius * math.cos(theta)
            y = radius * math.sin(theta)
            normal = Vector((x / 0.42, y / 0.42, v / 0.72)).normalized()
            tangent = Vector((-math.sin(theta), math.cos(theta), 0))
            color_mat = scale_mat if (i + ring) % 3 else body_mat
            scale = _diamond_mesh(
                "PineappleDiamond",
                (x, y, z),
                normal,
                tangent,
                width=0.070,
                height=0.105,
                mat=color_mat,
            )
            scale.rotation_euler.rotate_axis("Z", 0.10 * ((i % 2) * 2 - 1))

            bpy.ops.mesh.primitive_cone_add(
                vertices=8,
                radius1=0.012,
                radius2=0.0,
                depth=0.045,
                location=tuple(Vector((x, y, z)) + normal * 0.025),
            )
            thorn = bpy.context.active_object
            thorn.name = "PineappleThorn"
            thorn.rotation_euler = normal.to_track_quat("Z", "Y").to_euler()
            _assign(thorn, thorn_mat)


def _leaf_mesh(name, angle, length, width, base_z, lift, mat):
    direction = Vector((math.cos(angle), math.sin(angle), 0))
    side = Vector((-math.sin(angle), math.cos(angle), 0))
    base = Vector((0, 0, base_z))
    tip = direction * length + Vector((0, 0, base_z + lift))
    mid = direction * (length * 0.45) + Vector((0, 0, base_z + lift * 0.42))
    verts = [
        base + side * width,
        mid + side * width * 0.46,
        tip,
        mid - side * width * 0.46,
        base - side * width,
    ]
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata([tuple(v) for v in verts], [], [(0, 1, 2, 3, 4)])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    _assign(obj, mat)

    solidify = obj.modifiers.new("leaf_thickness", "SOLIDIFY")
    solidify.thickness = 0.006
    bevel = obj.modifiers.new("leaf_soft_edge", "BEVEL")
    bevel.width = 0.003
    bevel.segments = 1

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.modifier_apply(modifier=solidify.name)
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    obj.select_set(False)
    return obj


def _add_crown(leaf_mat, dark_leaf_mat):
    base_z = 1.22
    for layer in range(3):
        count = 10 - layer * 2
        length = 0.42 - layer * 0.08
        width = 0.055 - layer * 0.008
        lift = 0.18 + layer * 0.09
        for i in range(count):
            angle = 2 * math.pi * i / count + layer * 0.27
            mat = leaf_mat if (i + layer) % 2 else dark_leaf_mat
            leaf = _leaf_mesh(
                "PineappleCrownLeaf",
                angle,
                length,
                width,
                base_z + layer * 0.035,
                lift,
                mat,
            )
            leaf.rotation_euler.rotate_axis("Z", 0.04 * math.sin(i))

    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=24,
        ring_count=12,
        radius=0.075,
        location=(0, 0, base_z - 0.02),
    )
    core = bpy.context.active_object
    core.name = "PineappleCrownCore"
    core.scale = (1.0, 1.0, 0.6)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    _assign(core, dark_leaf_mat)


def build_scene():
    _clear_scene()
    body_mat = _mat("golden_pineapple_skin", (0.94, 0.66, 0.18, 1.0), 0.82)
    scale_mat = _mat("amber_raised_diamonds", (0.78, 0.47, 0.12, 1.0), 0.86)
    thorn_mat = _mat("brown_scale_tips", (0.27, 0.15, 0.06, 1.0), 0.9)
    leaf_mat = _mat("pineapple_leaf_green", (0.16, 0.42, 0.18, 1.0), 0.72)
    dark_leaf_mat = _mat("deep_leaf_green", (0.06, 0.25, 0.10, 1.0), 0.78)

    body = _add_body(body_mat)
    _add_pattern(body_mat, scale_mat, thorn_mat)
    _add_crown(leaf_mat, dark_leaf_mat)
    return [body]


if __name__ == "__main__":
    build_scene()
