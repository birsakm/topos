"""URDF writer for Topos articulated objects.

Inputs are plain dataclasses (or YAML/JSON-deserialised dicts that match the
shape). Mesh files are referenced by path relative to the URDF file's
directory; the writer does not move them.

Design choices (informed by LAM's ``utils/generate_urdf.py`` and the URDF spec):

- Joint ``origin`` is interpreted **in the parent link's frame**. The writer
  accumulates parent offsets so the *visual* origin of each link compensates,
  i.e. meshes can be authored at their world positions and the writer
  computes the right offset so they render at the correct place.
- Inertia is auto-filled with a reasonable default (1 kg point mass, identity
  inertia tensor). Override per-link if the user supplies one.
- Joints of type ``fixed`` don't need axis/limits but accept them harmlessly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal
from xml.etree import ElementTree as ET


JointType = Literal["fixed", "revolute", "prismatic", "continuous"]


@dataclass
class Link:
    name: str
    mesh_path: str                                 # relative to the .urdf file
    world_xyz: tuple[float, float, float] = (0.0, 0.0, 0.0)  # where the mesh was authored, in world frame
    world_rpy: tuple[float, float, float] = (0.0, 0.0, 0.0)
    mass: float = 1.0
    inertia_diag: tuple[float, float, float] = (1e-3, 1e-3, 1e-3)
    color_rgba: tuple[float, float, float, float] | None = None


@dataclass
class Joint:
    name: str
    type: JointType
    parent: str
    child: str
    origin_xyz: tuple[float, float, float] = (0.0, 0.0, 0.0)
    origin_rpy: tuple[float, float, float] = (0.0, 0.0, 0.0)
    axis: tuple[float, float, float] = (0.0, 0.0, 1.0)
    limit_lower: float = 0.0
    limit_upper: float = 0.0
    limit_effort: float = 10.0
    limit_velocity: float = 1.0


# ---------- conversion helpers ----------

Mat3 = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
Vec3 = tuple[float, float, float]


def _xyz(t: Iterable[float]) -> str:
    return " ".join(f"{v:.6g}" for v in t)


def _rpy_to_mat3(rpy: Vec3) -> Mat3:
    """URDF roll-pitch-yaw → 3x3 rotation matrix.

    URDF convention: R = Rz(yaw) @ Ry(pitch) @ Rx(roll), where roll=X, pitch=Y,
    yaw=Z and rotations are extrinsic (or equivalently intrinsic ZYX).
    """
    r, p, y = rpy
    cr, sr = math.cos(r), math.sin(r)
    cp, sp = math.cos(p), math.sin(p)
    cy, sy = math.cos(y), math.sin(y)
    return (
        (cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
        (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
        (-sp,     cp * sr,                cp * cr),
    )


def _mat3_to_rpy(m: Mat3) -> Vec3:
    """Inverse of _rpy_to_mat3. Handles gimbal lock at pitch=±π/2."""
    sp = -m[2][0]
    if sp >= 1.0 - 1e-9:
        return (math.atan2(m[0][1], m[1][1]), math.pi / 2, 0.0)
    if sp <= -1.0 + 1e-9:
        return (math.atan2(-m[0][1], m[1][1]), -math.pi / 2, 0.0)
    return (
        math.atan2(m[2][1], m[2][2]),
        math.asin(sp),
        math.atan2(m[1][0], m[0][0]),
    )


def _mat3_mul(a: Mat3, b: Mat3) -> Mat3:
    return tuple(
        tuple(a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j] for j in range(3))
        for i in range(3)
    )  # type: ignore[return-value]


def _mat3_vec(m: Mat3, v: Vec3) -> Vec3:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def _mat3_transpose(m: Mat3) -> Mat3:
    return ((m[0][0], m[1][0], m[2][0]),
            (m[0][1], m[1][1], m[2][1]),
            (m[0][2], m[1][2], m[2][2]))


def _se3_compose(Ra: Mat3, ta: Vec3, Rb: Mat3, tb: Vec3) -> tuple[Mat3, Vec3]:
    """(Ra, ta) ∘ (Rb, tb): rotates+translates Rb,tb by Ra,ta."""
    R = _mat3_mul(Ra, Rb)
    Ra_tb = _mat3_vec(Ra, tb)
    t = (Ra_tb[0] + ta[0], Ra_tb[1] + ta[1], Ra_tb[2] + ta[2])
    return R, t


def _se3_inverse(R: Mat3, t: Vec3) -> tuple[Mat3, Vec3]:
    Rt = _mat3_transpose(R)
    nRt_t = _mat3_vec(Rt, (-t[0], -t[1], -t[2]))
    return Rt, nRt_t


def _accumulate_joint_world_poses(joints: list[Joint]) -> dict[str, tuple[Mat3, Vec3]]:
    """For each link, compute its joint origin's pose (rotation + translation)
    expressed in the world frame, by composing along the parent → child chain.

    URDF joint origins are specified in the parent link frame, so the child
    link's joint-world pose is parent_joint_world ∘ joint_origin. Root links
    (no incoming joint) get identity pose.
    """
    parent_of: dict[str, Joint] = {j.child: j for j in joints}
    identity_R: Mat3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    cache: dict[str, tuple[Mat3, Vec3]] = {}

    def resolve(link_name: str) -> tuple[Mat3, Vec3]:
        if link_name in cache:
            return cache[link_name]
        if link_name not in parent_of:  # root
            cache[link_name] = (identity_R, (0.0, 0.0, 0.0))
            return cache[link_name]
        j = parent_of[link_name]
        Rp, tp = resolve(j.parent)
        Rj = _rpy_to_mat3(j.origin_rpy)
        R, t = _se3_compose(Rp, tp, Rj, j.origin_xyz)
        cache[link_name] = (R, t)
        return cache[link_name]

    all_links = {j.parent for j in joints} | {j.child for j in joints}
    for n in all_links:
        resolve(n)
    return cache


# ---------- writer ----------

def build_urdf_element(
    robot_name: str,
    links: list[Link],
    joints: list[Joint],
) -> ET.Element:
    """Build the XML tree for a URDF ``<robot>`` element."""
    root = ET.Element("robot", {"name": robot_name})
    link_by_name = {link.name: link for link in links}
    joint_world_poses = _accumulate_joint_world_poses(joints)
    identity_R: Mat3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

    # ---- links ----
    for link in links:
        # Express the link's mesh pose (authored in world frame) in its joint
        # frame: visual_in_joint = inv(joint_world) ∘ mesh_world. This gives
        # the correct URDF <visual><origin> regardless of how the parent chain
        # rotates the joint.
        joint_R, joint_t = joint_world_poses.get(link.name, (identity_R, (0.0, 0.0, 0.0)))
        mesh_R = _rpy_to_mat3(link.world_rpy)
        Rj_inv, tj_inv = _se3_inverse(joint_R, joint_t)
        Rv, tv = _se3_compose(Rj_inv, tj_inv, mesh_R, link.world_xyz)
        visual_xyz = tv
        visual_rpy = _mat3_to_rpy(Rv)
        link_el = ET.SubElement(root, "link", {"name": link.name})

        for tag in ("visual", "collision"):
            sub = ET.SubElement(link_el, tag)
            ET.SubElement(sub, "origin", {
                "xyz": _xyz(visual_xyz),
                "rpy": _xyz(visual_rpy),
            })
            geom = ET.SubElement(sub, "geometry")
            ET.SubElement(geom, "mesh", {"filename": link.mesh_path})

            if tag == "visual" and link.color_rgba is not None:
                material = ET.SubElement(sub, "material", {"name": f"{link.name}_mat"})
                ET.SubElement(material, "color", {"rgba": _xyz(link.color_rgba)})

        inertial = ET.SubElement(link_el, "inertial")
        ET.SubElement(inertial, "origin", {"xyz": "0 0 0", "rpy": "0 0 0"})
        ET.SubElement(inertial, "mass", {"value": f"{link.mass:.6g}"})
        ixx, iyy, izz = link.inertia_diag
        ET.SubElement(inertial, "inertia", {
            "ixx": f"{ixx:.6g}", "ixy": "0", "ixz": "0",
            "iyy": f"{iyy:.6g}", "iyz": "0",
            "izz": f"{izz:.6g}",
        })

    # ---- joints ----
    for j in joints:
        if j.parent not in link_by_name or j.child not in link_by_name:
            raise ValueError(
                f"joint {j.name!r} references unknown link "
                f"(parent={j.parent!r}, child={j.child!r}); known: {list(link_by_name)}"
            )
        joint_el = ET.SubElement(root, "joint", {"name": j.name, "type": j.type})
        ET.SubElement(joint_el, "parent", {"link": j.parent})
        ET.SubElement(joint_el, "child", {"link": j.child})
        ET.SubElement(joint_el, "origin", {
            "xyz": _xyz(j.origin_xyz),
            "rpy": _xyz(j.origin_rpy),
        })
        if j.type in ("revolute", "prismatic", "continuous"):
            ET.SubElement(joint_el, "axis", {"xyz": _xyz(j.axis)})
        if j.type in ("revolute", "prismatic"):
            ET.SubElement(joint_el, "limit", {
                "lower": f"{j.limit_lower:.6g}",
                "upper": f"{j.limit_upper:.6g}",
                "effort": f"{j.limit_effort:.6g}",
                "velocity": f"{j.limit_velocity:.6g}",
            })

    return root


def write_urdf(
    robot_name: str,
    links: list[Link],
    joints: list[Joint],
    output_path: Path,
) -> Path:
    """Serialise a URDF file. Returns the resolved output path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    root = build_urdf_element(robot_name, links, joints)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ", level=0)  # pretty print (Python 3.9+)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path


# ---------- dict-shape entry (for YAML-driven plans) ----------

def from_dict(spec: dict[str, Any]) -> tuple[str, list[Link], list[Joint]]:
    """Materialise a spec dict (typically loaded from YAML/JSON) into typed
    dataclasses suitable for ``write_urdf``.

    Expected shape::

        robot: my_cabinet            # optional, defaults to "robot"
        links:
          - name: frame
            mesh_path: parts/frame.obj
            world_xyz: [0, 0, 0]
            world_rpy: [0, 0, 0]
            color_rgba: [0.45, 0.27, 0.15, 1.0]
          - name: drawer
            mesh_path: parts/drawer.obj
            world_xyz: [0, 0.15, 0.25]
        joints:
          - name: drawer_slide
            type: prismatic
            parent: frame
            child: drawer
            origin_xyz: [0, 0.0, 0.25]
            axis: [0, 1, 0]
            limit_lower: 0.0
            limit_upper: 0.25
    """
    name = spec.get("robot", "robot")
    links = [Link(**_link_kwargs(d)) for d in spec.get("links") or []]
    joints = [Joint(**_joint_kwargs(d)) for d in spec.get("joints") or []]
    return name, links, joints


def _coerce_xyz(v: Any) -> tuple[float, float, float]:
    return tuple(float(x) for x in v[:3])  # type: ignore[return-value]


def _link_kwargs(d: dict) -> dict:
    kw = dict(d)
    for k in ("world_xyz", "world_rpy", "inertia_diag"):
        if k in kw and kw[k] is not None:
            kw[k] = _coerce_xyz(kw[k])
    if kw.get("color_rgba") is not None:
        kw["color_rgba"] = tuple(float(x) for x in kw["color_rgba"][:4])
    return kw


def _joint_kwargs(d: dict) -> dict:
    kw = dict(d)
    for k in ("origin_xyz", "origin_rpy", "axis"):
        if k in kw and kw[k] is not None:
            kw[k] = _coerce_xyz(kw[k])
    return kw
