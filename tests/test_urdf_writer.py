"""Unit tests for the URDF writer."""

from __future__ import annotations

import math
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from topos.urdf import Joint, Link, build_urdf_element, from_dict, write_urdf


def test_minimal_two_link_prismatic(tmp_path: Path):
    links = [
        Link(name="frame", mesh_path="parts/frame.obj"),
        Link(name="drawer", mesh_path="parts/drawer.obj",
             world_xyz=(0.0, 0.15, 0.25)),
    ]
    joints = [
        Joint(
            name="drawer_slide", type="prismatic",
            parent="frame", child="drawer",
            origin_xyz=(0.0, 0.0, 0.25),
            axis=(0.0, 1.0, 0.0),
            limit_lower=0.0, limit_upper=0.25,
        ),
    ]
    out = write_urdf("test_cabinet", links, joints, tmp_path / "out.urdf")
    assert out.is_file()
    tree = ET.parse(out)
    root = tree.getroot()
    assert root.tag == "robot"
    assert root.get("name") == "test_cabinet"

    link_names = [el.get("name") for el in root.findall("link")]
    assert link_names == ["frame", "drawer"]

    joint_els = root.findall("joint")
    assert len(joint_els) == 1
    j = joint_els[0]
    assert j.get("type") == "prismatic"
    assert j.find("parent").get("link") == "frame"
    assert j.find("child").get("link") == "drawer"
    assert j.find("limit").get("upper") == "0.25"


def test_visual_origin_compensates_joint_origin(tmp_path: Path):
    """When the mesh is authored at world (0, 0.15, 0.25) and the joint origin
    in the parent frame is (0, 0, 0.25), the visual origin in the URDF must be
    (0, 0.15, 0) — i.e. mesh_world minus joint_world (also (0,0,0.25) since
    parent is root)."""
    links = [
        Link(name="frame", mesh_path="frame.obj"),
        Link(name="drawer", mesh_path="drawer.obj",
             world_xyz=(0.0, 0.15, 0.25)),
    ]
    joints = [
        Joint(name="drawer_slide", type="prismatic",
              parent="frame", child="drawer",
              origin_xyz=(0.0, 0.0, 0.25),
              axis=(0.0, 1.0, 0.0)),
    ]
    root = build_urdf_element("c", links, joints)
    drawer = next(el for el in root.findall("link") if el.get("name") == "drawer")
    visual_origin = drawer.find("visual/origin").get("xyz").split()
    assert [float(x) for x in visual_origin] == pytest.approx([0.0, 0.15, 0.0])


def test_fixed_joint_omits_axis_and_limit(tmp_path: Path):
    links = [
        Link(name="drawer", mesh_path="drawer.obj"),
        Link(name="handle", mesh_path="handle.obj",
             world_xyz=(0.0, 0.21, 0.25)),
    ]
    joints = [
        Joint(name="handle_attach", type="fixed",
              parent="drawer", child="handle",
              origin_xyz=(0.0, 0.21, 0.0)),
    ]
    out = write_urdf("c", links, joints, tmp_path / "f.urdf")
    tree = ET.parse(out)
    root = tree.getroot()
    j = next(el for el in root.findall("joint") if el.get("type") == "fixed")
    assert j.find("axis") is None
    assert j.find("limit") is None


def test_joint_references_unknown_link_raises(tmp_path: Path):
    links = [Link(name="a", mesh_path="a.obj")]
    joints = [Joint(name="bad", type="fixed", parent="a", child="ghost")]
    with pytest.raises(ValueError, match="ghost"):
        build_urdf_element("r", links, joints)


def test_from_dict_roundtrip(tmp_path: Path):
    spec = {
        "robot": "drawer_cabinet",
        "links": [
            {"name": "frame", "mesh_path": "parts/frame.obj"},
            {"name": "drawer", "mesh_path": "parts/drawer.obj",
             "world_xyz": [0.0, 0.15, 0.25],
             "color_rgba": [0.4, 0.25, 0.15, 1.0]},
        ],
        "joints": [
            {"name": "drawer_slide", "type": "prismatic",
             "parent": "frame", "child": "drawer",
             "origin_xyz": [0.0, 0.0, 0.25],
             "axis": [0, 1, 0],
             "limit_lower": 0.0, "limit_upper": 0.25},
        ],
    }
    name, links, joints = from_dict(spec)
    assert name == "drawer_cabinet"
    assert len(links) == 2 and len(joints) == 1
    assert links[1].color_rgba == (0.4, 0.25, 0.15, 1.0)
    out = write_urdf(name, links, joints, tmp_path / "x.urdf")
    assert out.is_file()


def test_rotated_joint_origin_compensates_child_visual(tmp_path: Path):
    """Parent joint has origin_rpy=(0,0,π/2) yaw: a child mesh authored at
    world (1,1,0) must appear at xyz=(1,-1,0), rpy=(0,0,-π/2) in the joint's
    rotated frame. This exercises the full SE(3) inverse-compose path —
    pure-translation tests can't catch a transpose/sign bug in the rotation
    math."""
    links = [
        Link(name="root", mesh_path="root.obj"),
        Link(name="child", mesh_path="child.obj", world_xyz=(1.0, 1.0, 0.0)),
    ]
    joints = [
        Joint(name="j", type="fixed", parent="root", child="child",
              origin_xyz=(0.0, 0.0, 0.0),
              origin_rpy=(0.0, 0.0, math.pi / 2)),
    ]
    root = build_urdf_element("r", links, joints)
    child = next(el for el in root.findall("link") if el.get("name") == "child")
    visual = child.find("visual/origin")
    xyz = [float(x) for x in visual.get("xyz").split()]
    rpy = [float(x) for x in visual.get("rpy").split()]
    # URDF serialises floats as "%.6g" so expected values match to ~6 sig figs.
    assert xyz == pytest.approx([1.0, -1.0, 0.0], abs=1e-4)
    assert rpy == pytest.approx([0.0, 0.0, -math.pi / 2], abs=1e-4)


def test_mesh_world_rpy_passes_through_under_identity_joint(tmp_path: Path):
    """Identity-rotation joint: a mesh's world_rpy must appear verbatim in
    visual/origin. Guards against any accidental swap in _mat3_to_rpy ↔
    _rpy_to_mat3 (the round-trip identity must hold)."""
    links = [
        Link(name="root", mesh_path="root.obj"),
        Link(name="child", mesh_path="child.obj",
             world_xyz=(0.0, 0.0, 0.0),
             world_rpy=(0.1, 0.2, 0.3)),
    ]
    joints = [
        Joint(name="j", type="fixed", parent="root", child="child"),
    ]
    root = build_urdf_element("r", links, joints)
    child = next(el for el in root.findall("link") if el.get("name") == "child")
    visual = child.find("visual/origin")
    xyz = [float(x) for x in visual.get("xyz").split()]
    rpy = [float(x) for x in visual.get("rpy").split()]
    assert xyz == pytest.approx([0.0, 0.0, 0.0], abs=1e-4)
    assert rpy == pytest.approx([0.1, 0.2, 0.3], abs=1e-4)


def test_chained_yaw_joints_compose_correctly(tmp_path: Path):
    """Two yaw-90° rotations in series compose to yaw-180°. A grandchild mesh
    authored at world (1,0,0) should appear at xyz=(-1,0,0), rpy yaw=±π in
    the grandchild's joint frame. Verifies the parent-chain composition in
    _accumulate_joint_world_poses is correct."""
    links = [
        Link(name="a", mesh_path="a.obj"),
        Link(name="b", mesh_path="b.obj"),
        Link(name="c", mesh_path="c.obj", world_xyz=(1.0, 0.0, 0.0)),
    ]
    joints = [
        Joint(name="j_ab", type="fixed", parent="a", child="b",
              origin_xyz=(0.0, 0.0, 0.0),
              origin_rpy=(0.0, 0.0, math.pi / 2)),
        Joint(name="j_bc", type="fixed", parent="b", child="c",
              origin_xyz=(0.0, 0.0, 0.0),
              origin_rpy=(0.0, 0.0, math.pi / 2)),
    ]
    root = build_urdf_element("r", links, joints)
    c = next(el for el in root.findall("link") if el.get("name") == "c")
    visual = c.find("visual/origin")
    xyz = [float(x) for x in visual.get("xyz").split()]
    rpy = [float(x) for x in visual.get("rpy").split()]
    assert xyz == pytest.approx([-1.0, 0.0, 0.0], abs=1e-4)
    # yaw = ±π are the same rotation; just check magnitude
    assert abs(rpy[2]) == pytest.approx(math.pi, abs=1e-4)
    assert rpy[0] == pytest.approx(0.0, abs=1e-4)
    assert rpy[1] == pytest.approx(0.0, abs=1e-4)


def test_rotated_joint_with_translation_offset(tmp_path: Path):
    """Realistic case: a part attached via a joint that has BOTH rotation and
    translation. Parent joint at xyz=(1,0,0) rpy=(0,0,π/2); child mesh authored
    at world (1, 0.5, 0) — i.e. 0.5 along the rotated +x axis from the joint.
    Visual origin in joint frame must be (0.5, 0, 0) rpy=(0,0,-π/2)."""
    links = [
        Link(name="root", mesh_path="root.obj"),
        Link(name="child", mesh_path="child.obj", world_xyz=(1.0, 0.5, 0.0)),
    ]
    joints = [
        Joint(name="j", type="fixed", parent="root", child="child",
              origin_xyz=(1.0, 0.0, 0.0),
              origin_rpy=(0.0, 0.0, math.pi / 2)),
    ]
    root = build_urdf_element("r", links, joints)
    child = next(el for el in root.findall("link") if el.get("name") == "child")
    visual = child.find("visual/origin")
    xyz = [float(x) for x in visual.get("xyz").split()]
    rpy = [float(x) for x in visual.get("rpy").split()]
    assert xyz == pytest.approx([0.5, 0.0, 0.0], abs=1e-4)
    assert rpy == pytest.approx([0.0, 0.0, -math.pi / 2], abs=1e-4)


def test_three_link_chain_accumulates_offsets(tmp_path: Path):
    """frame at (0,0,0) → drawer joint at (0,0,0.5) → handle joint at (0,0.2,0)
    in drawer's frame. Handle mesh authored at world (0, 0.2, 0.5).
    The handle's link-world origin = (0,0,0.5)+(0,0.2,0)=(0,0.2,0.5).
    Visual origin = mesh_world - link_world = (0,0,0)."""
    links = [
        Link(name="frame", mesh_path="frame.obj"),
        Link(name="drawer", mesh_path="drawer.obj",
             world_xyz=(0.0, 0.0, 0.5)),
        Link(name="handle", mesh_path="handle.obj",
             world_xyz=(0.0, 0.2, 0.5)),
    ]
    joints = [
        Joint(name="slide", type="prismatic", parent="frame", child="drawer",
              origin_xyz=(0.0, 0.0, 0.5), axis=(0, 1, 0)),
        Joint(name="attach", type="fixed", parent="drawer", child="handle",
              origin_xyz=(0.0, 0.2, 0.0)),
    ]
    root = build_urdf_element("c", links, joints)
    handle = next(el for el in root.findall("link") if el.get("name") == "handle")
    visual_origin = [float(x) for x in handle.find("visual/origin").get("xyz").split()]
    assert visual_origin == pytest.approx([0.0, 0.0, 0.0])
