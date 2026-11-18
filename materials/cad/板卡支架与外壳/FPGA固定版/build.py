"""Generate the printable parts and the fitted reference assembly, in millimetres."""

from pathlib import Path

import manifold3d as m3d
import numpy as np
import trimesh


OUT = Path(__file__).resolve().parent
BASE_Z = 5.0
RK_STANDOFF = 20.0
RK_THICKNESS = 1.6
RK_FRONT = BASE_Z + RK_STANDOFF
RK_REAR = RK_FRONT + RK_THICKNESS
RK_HOLES = [(2.5, 2.5), (147.5, 2.5), (2.5, 97.5), (147.5, 97.5)]

# Viewed from the back of RK03: +X runs from its terminal end toward the SIM end,
# +Y from the HDMI/DC edge toward the Ethernet edge, +Z toward the PH47.
M2_MOUTH_X = 71.1
M2_CENTER_Y = 47.2
ADAPTER_TAIL_X = 148.0
PCIE_CENTER_Y = 39.5
FPGA_SFP_X = 164.0
FPGA_SHOULDER_Z = RK_REAR + 14.0
FPGA_THICKNESS = 1.6
BRACE_FACE_Y = 32.0
BRACE_THICKNESS = 7.0
BRACE_ANCHOR_TRAVEL = 10.0
BRACE_SLOT_TRAVEL = 12.0
BRACE_MOUNTS = [(-13, 3, 2), (180, 0, 1)]
ROOF_Z = 124.0
ROOF_THICKNESS = 3.2
WALL = 3.0

X_MIN, X_MAX = -22.0, 193.0
Y_MIN, Y_MAX = -13.0, 113.0
COVER_SCREWS = [(-14.0, -5.0), (185.0, -5.0), (-14.0, 105.0), (185.0, 105.0)]

# Outline and corner holes from MLK-PFAC100.dxf, BG_DESIGN_OUTLINE / drill table.
FPGA_OUTLINE = [
    (58.5998, -4.5001), (58.1, -4), (58.1, 2.95), (57.15, 3.8999),
    (56.2, 2.95), (56.2, -4), (55.6999, -4.5001), (45.5, -4.5001),
    (44.9999, -4), (44.9999, 6.4249), (43.1749, 8.2499),
    (41.3499, 6.4249), (41.3499, 0), (33.3499, 0), (33.3499, 8.2499),
    (15, 8.2499), (15, 0), (0, 0), (0, 64.4), (110, 64.4),
    (110, 8.306), (96.298, 8.306), (96.298, -3.921), (95.7189, -4.5001),
]
FPGA_HOLES = [(3, 3), (3, 61.4), (107, 61.4), (107, 11.306)]


def box(x0, x1, y0, y1, z0, z1):
    return m3d.Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def cyl(x, y, z, diameter, height):
    return m3d.Manifold.cylinder(height, diameter / 2, circular_segments=40).translate((x, y, z))


def profile_in_xz(points, y_min, y_max):
    section = m3d.CrossSection([points], m3d.FillRule.EvenOdd)
    return section.extrude(y_max - y_min).rotate((90, 0, 0)).translate((0, y_max, 0))


def x_slot(x, y, z, travel, diameter, height):
    x0, x1 = x - travel / 2, x + travel / 2
    radius = diameter / 2
    return (cyl(x0, y, z, diameter, height) + cyl(x1, y, z, diameter, height)
            + box(x0, x1, y - radius, y + radius, z, z + height))


def brace_anchor_points(anchor_x):
    return [(anchor_x, BRACE_FACE_Y - 19), (anchor_x, BRACE_FACE_Y - 11)]


def build_base():
    base = box(X_MIN, X_MAX, Y_MIN, Y_MAX, 0, BASE_Z)
    base -= box(12, 138, 12, 88, -1, BASE_Z + 1)
    for x, y in RK_HOLES:
        base -= cyl(x, y, -1, 4.8, BASE_Z + 2)
    for x, y in COVER_SCREWS:
        base -= cyl(x, y, -1, 3.5, BASE_Z + 2)
    for anchor_x, _, _ in BRACE_MOUNTS:
        for x, y in brace_anchor_points(anchor_x):
            base -= x_slot(x, y, -1, BRACE_ANCHOR_TRAVEL, 3.6, BASE_Z + 2)
    return base


def y_bore(x, y0, y1, z, diameter):
    return cyl(x, -z, 0, diameter, y1 - y0).rotate((-90, 0, 0)).translate((0, y0, 0))


def build_brace(anchor_x, lower_index, upper_index):
    local_x, lower_z = FPGA_HOLES[lower_index]
    hole_x = FPGA_SFP_X - local_x
    lower_z += FPGA_SHOULDER_Z
    upper_z = FPGA_SHOULDER_Z + FPGA_HOLES[upper_index][1]
    back_y, front_y = BRACE_FACE_Y - BRACE_THICKNESS, BRACE_FACE_Y
    link_left, link_right = sorted((anchor_x, hole_x))
    lower_edge = max(RK_REAR + 9, lower_z - 11.3)

    # A frame in the plane of the PCB. Its foot stays on the base when the lid is removed.
    root = box(anchor_x - 5.5, anchor_x + 5.5, back_y, front_y, BASE_Z + 3.5, upper_z - 12)
    rail = box(hole_x - 8, hole_x + 8, back_y, front_y, lower_z - 11.3, upper_z + 11.3)
    lower_link = box(link_left, link_right, back_y, front_y, lower_edge, lower_edge + 12)
    upper_link = profile_in_xz([
        (anchor_x, upper_z - 28), (hole_x, upper_z - 8),
        (hole_x, upper_z + 4), (anchor_x, upper_z - 13),
    ], back_y, front_y)
    foot = box(anchor_x - 5.5, anchor_x + 5.5,
               BRACE_FACE_Y - 24, BRACE_FACE_Y, BASE_Z, BASE_Z + 5)
    brace = root + rail + lower_link + upper_link + foot
    for x, y in brace_anchor_points(anchor_x):
        brace -= cyl(x, y, BASE_Z - 1, 3.6, 7)

    y0, y1 = back_y - 1, front_y + 1
    for hole_z in (lower_z, upper_z):
        z0 = hole_z - BRACE_SLOT_TRAVEL / 2
        z1 = hole_z + BRACE_SLOT_TRAVEL / 2
        brace -= (y_bore(hole_x, y0, y1, z0, 3.6) + y_bore(hole_x, y0, y1, z1, 3.6)
                  + box(hole_x - 1.8, hole_x + 1.8, y0, y1, z0, z1))
    return brace


def build_cover():
    inset = 0.6
    x0, x1 = X_MIN + inset, X_MAX - inset
    y0, y1 = Y_MIN + inset, Y_MAX - inset
    lid = box(x0, x1, y0, y1, BASE_Z, ROOF_Z)
    lid -= box(x0 + WALL, x1 - WALL, y0 + WALL, y1 - WALL,
               BASE_Z - 1, ROOF_Z - ROOF_THICKNESS)

    # Open-bottom cable bays print as open slots with the roof on the bed.
    lid -= box(-4, 158, y0 - 1, y0 + WALL + 1, BASE_Z - 1, RK_REAR + 9)
    lid -= box(-4, 158, y1 - WALL - 1, y1 + 1, BASE_Z - 1, RK_REAR + 9)
    lid -= box(x0 - 1, x0 + WALL + 1, 6, 88, BASE_Z - 1, 110)
    lid -= box(x1 - WALL - 1, x1 + 1, 6, 88, BASE_Z - 1, 101)
    # The original PCIe metal bracket has a tab above the PCB. Leave its corner open.
    lid -= box(FPGA_SFP_X - 10, x1 + 1, PCIE_CENTER_Y - 7, PCIE_CENTER_Y + 29,
               100, ROOF_Z + 1)

    # Roof opening above CSI. The camera stays outside, with room for its FFC bend.
    csi_x = FPGA_SFP_X - 77
    lid -= box(csi_x - 20, csi_x + 20, PCIE_CENTER_Y + 3, PCIE_CENTER_Y + 20,
               ROOF_Z - ROOF_THICKNESS - 1, ROOF_Z + 1)
    for x in (10, 26, 42, 119, 135, 146):
        lid -= box(x, x + 6, 3, 32, ROOF_Z - ROOF_THICKNESS - 1, ROOF_Z + 1)
        lid -= box(x, x + 6, 62, 96, ROOF_Z - ROOF_THICKNESS - 1, ROOF_Z + 1)

    # External roof seats for a cable tie or a removable camera bracket.
    for x in (csi_x - 28, csi_x + 28):
        for y in (48, 60):
            lid -= cyl(x, y, ROOF_Z - ROOF_THICKNESS - 1, 3.5, ROOF_THICKNESS + 2)

    # Corner columns run continuously to the roof. Seat M3 heat-set inserts from below.
    for x, y in COVER_SCREWS:
        lid += cyl(x, y, BASE_Z, 10.8, ROOF_Z - BASE_Z - 1.0)
        lid -= cyl(x, y, BASE_Z - 1, 4.2, 7.0)
    return lid


def build_reference_hardware():
    rk = box(0, 150, 0, 100, RK_FRONT, RK_REAR)
    standoffs = []
    for x, y in RK_HOLES:
        rk -= cyl(x, y, RK_FRONT - 1, 3.0, RK_THICKNESS + 2)
        standoffs.append(cyl(x, y, BASE_Z, 5.5, RK_STANDOFF))

    # Envelopes for checking fit, based on the manuals and assembly photos.
    rk_heatsink = box(32, 96, 29, 69, RK_FRONT - 12, RK_FRONT)
    rk_ports = [
        box(4, 14, -2, 11, RK_FRONT - 10, RK_FRONT),
        box(33, 50, -2, 12, RK_FRONT - 7, RK_FRONT),
        box(62, 79, -2, 13, RK_FRONT - 8, RK_FRONT),
        box(83, 96, 87, 103, RK_FRONT - 15, RK_FRONT),
        box(105, 118, 87, 103, RK_FRONT - 15, RK_FRONT),
        box(127, 143, 88, 103, RK_FRONT - 14, RK_FRONT),
    ]
    m2 = box(62.5, M2_MOUTH_X, M2_CENTER_Y - 11.5, M2_CENTER_Y + 11.5, RK_REAR, RK_REAR + 4)
    adapter = box(M2_MOUTH_X - 3, ADAPTER_TAIL_X, M2_CENTER_Y - 11, M2_CENTER_Y + 11,
                  RK_REAR + 3.8, RK_REAR + 5.4)
    socket = box(85, 124.5, PCIE_CENTER_Y - 3.6, PCIE_CENTER_Y + 3.6,
                 RK_REAR + 5.4, FPGA_SHOULDER_Z + 2.4)
    socket -= box(84.5, 125.0, PCIE_CENTER_Y - 1.05, PCIE_CENTER_Y + 1.05,
                  FPGA_SHOULDER_Z - 5, FPGA_SHOULDER_Z + 3)

    fpga = profile_in_xz([(FPGA_SFP_X - x, FPGA_SHOULDER_Z + z) for x, z in FPGA_OUTLINE],
                         PCIE_CENTER_Y - FPGA_THICKNESS / 2, PCIE_CENTER_Y + FPGA_THICKNESS / 2)
    for x, z in FPGA_HOLES:
        fpga -= y_bore(FPGA_SFP_X - x, PCIE_CENTER_Y - FPGA_THICKNESS / 2 - 1,
                       PCIE_CENTER_Y + FPGA_THICKNESS / 2 + 1, FPGA_SHOULDER_Z + z, 3.048)

    front_y = PCIE_CENTER_Y + FPGA_THICKNESS / 2
    shield = box(FPGA_SFP_X, FPGA_SFP_X + 2, front_y - 1, front_y + 19,
                 FPGA_SHOULDER_Z - 16, FPGA_SHOULDER_Z + 84)
    shield += box(FPGA_SFP_X, FPGA_SFP_X + 13, front_y - 1, front_y + 19,
                  FPGA_SHOULDER_Z + 82, FPGA_SHOULDER_Z + 84)
    sfp = box(FPGA_SFP_X - 40, FPGA_SFP_X + 2, front_y, front_y + 11.5,
              FPGA_SHOULDER_Z + 12, FPGA_SHOULDER_Z + 43)
    sink = box(FPGA_SFP_X - 90, FPGA_SFP_X - 35, front_y, front_y + 12,
               FPGA_SHOULDER_Z + 13, FPGA_SHOULDER_Z + 48)
    power = box(FPGA_SFP_X - 112, FPGA_SFP_X - 97, front_y, front_y + 11,
                FPGA_SHOULDER_Z + 43, FPGA_SHOULDER_Z + 60)
    csi = box(FPGA_SFP_X - 87, FPGA_SFP_X - 67, front_y, front_y + 3,
              FPGA_SHOULDER_Z + 59, FPGA_SHOULDER_Z + 63)
    ffc_start_y = front_y + 2
    ffc_start_z = FPGA_SHOULDER_Z + 63
    ffc = m3d.CrossSection([[
        (ffc_start_y, ffc_start_z), (ffc_start_y, ffc_start_z + 5),
        (PCIE_CENTER_Y + 14, ffc_start_z + 12), (PCIE_CENTER_Y + 14, ROOF_Z + 9),
        (PCIE_CENTER_Y + 14.4, ROOF_Z + 9), (PCIE_CENTER_Y + 14.4, ffc_start_z + 11.8),
        (ffc_start_y + 0.4, ffc_start_z + 4.8), (ffc_start_y + 0.4, ffc_start_z),
    ]], m3d.FillRule.EvenOdd).extrude(19).rotate((90, 0, 90)).translate((FPGA_SFP_X - 86.5, 0, 0))
    rtc = cyl(43.8, 35.6, RK_REAR, 16.0, 5.5)
    return {
        'RK03 PCB': (rk, '#21866d'), 'RK heatsink': (rk_heatsink, '#48545d'),
        'RK ports': (m3d.Manifold.batch_boolean(rk_ports, m3d.OpType.Add), '#b4bec7'),
        'RK standoffs': (m3d.Manifold.batch_boolean(standoffs, m3d.OpType.Add), '#cfab60'),
        'RTC battery': (rtc, '#bdc7ca'), 'RK M2 socket': (m2, '#202730'),
        'PH47 adapter PCB': (adapter, '#324458'), 'PH47 PCIe slot': (socket, '#253247'),
        'PFAC100 PCB': (fpga, '#209b77'), 'FPGA I-O bracket': (shield, '#adbac8'),
        'FPGA SFP cages': (sfp, '#bbc8d3'), 'FPGA heatsink': (sink, '#55656f'),
        'FPGA 12V connector': (power, '#30373f'), 'FPGA CSI connector': (csi, '#ebe1c9'),
        'Camera FFC route': (ffc, '#f0ba57'),
    }


def mesh(solid, color=None):
    raw = solid.to_mesh()
    result = trimesh.Trimesh(vertices=raw.vert_properties[:, :3], faces=raw.tri_verts)
    if color:
        result.visual.face_colors = np.array([*bytes.fromhex(color.lstrip('#')), 255])
    return result


def brace_hardware():
    pcb_back_y = PCIE_CENTER_Y - FPGA_THICKNESS / 2
    brace_back_y = BRACE_FACE_Y - BRACE_THICKNESS
    spacers, screws = [], []
    for local_x, local_z in FPGA_HOLES:
        x = FPGA_SFP_X - local_x
        z = FPGA_SHOULDER_Z + local_z
        spacer = y_bore(x, BRACE_FACE_Y, pcb_back_y, z, 5.5)
        spacer -= y_bore(x, BRACE_FACE_Y - 1, pcb_back_y + 1, z, 3.1)
        spacers.append(spacer)
        head = y_bore(x, brace_back_y - 2.6, brace_back_y, z, 5.6)
        shaft = y_bore(x, brace_back_y, BRACE_FACE_Y + 3.5, z, 2.9)
        screws.append(head + shaft)
    return {
        'FPGA rear standoffs': (m3d.Manifold.batch_boolean(spacers, m3d.OpType.Add), '#c8a363'),
        'FPGA mount screws': (m3d.Manifold.batch_boolean(screws, m3d.OpType.Add), '#bcc4ca'),
    }


def main():
    parts = {
        '01_base': (build_base(), '#35516b', (0, 0, 0)),
        '02_fpga_power_end_brace': (build_brace(*BRACE_MOUNTS[0]), '#c66d38', (-90, 0, 0)),
        '03_fpga_sfp_end_brace': (build_brace(*BRACE_MOUNTS[1]), '#c66d38', (-90, 0, 0)),
        '04_removable_cover': (build_cover(), '#5c748c', (180, 0, 0)),
    }
    hardware = build_reference_hardware() | brace_hardware()
    for name, (solid, _, orientation) in parts.items():
        printable = mesh(solid.rotate(orientation))
        assert printable.is_volume and printable.body_count == 1, name
        printable.apply_translation(-printable.bounds[0])
        printable.export(OUT / f'{name}.stl')
        reloaded = trimesh.load(OUT / f'{name}.stl', force='mesh')
        assert reloaded.is_volume and reloaded.body_count == 1, name
        print(f'{name:27s}  {printable.extents.round(2)} mm   {printable.volume / 1000:.1f} cm3')

    # Allow fastener contact only. Check the envelopes of every solid independently.
    for name, (part, _, _) in parts.items():
        for other, (shape, _) in hardware.items():
            volume = (part ^ shape).volume()
            assert volume < 0.01, f'{name} hits {other}: {volume:.2f} mm3'
    for name, (part, _, _) in parts.items():
        for other, (shape, _, _) in parts.items():
            if name < other:
                assert (part ^ shape).volume() < 0.01, f'{name} hits {other}'

    for name, (anchor_x, lower, upper) in zip(list(parts)[1:3], BRACE_MOUNTS):
        brace = parts[name][0]
        for offset in (-BRACE_ANCHOR_TRAVEL / 2, BRACE_ANCHOR_TRAVEL / 2):
            moved = brace.translate((offset, 0, 0))
            assert (moved ^ parts['04_removable_cover'][0]).volume() < 0.01, (name, offset)
            for x, y in brace_anchor_points(anchor_x):
                pin = cyl(x + offset, y, -1, 2.9, BASE_Z + 7)
                assert (pin ^ (moved + parts['01_base'][0])).volume() < 0.01, (name, offset)
        for index in (lower, upper):
            local_x, local_z = FPGA_HOLES[index]
            for offset in (-BRACE_SLOT_TRAVEL / 2, BRACE_SLOT_TRAVEL / 2):
                pin = y_bore(FPGA_SFP_X - local_x, BRACE_FACE_Y - BRACE_THICKNESS - 1,
                             BRACE_FACE_Y + 1, FPGA_SHOULDER_Z + local_z + offset, 2.9)
                assert (pin ^ brace).volume() < 0.01, (name, index, offset)

    scene = trimesh.Scene()
    for name, (solid, color, _) in parts.items():
        if name != '04_removable_cover':
            scene.add_geometry(mesh(solid, color), node_name=name, geom_name=name)
    for name, (solid, color) in hardware.items():
        scene.add_geometry(mesh(solid, color), node_name=name, geom_name=name)
    # Lift the cover in the assembly preview to reveal the insertion stack.
    cover, color, _ = parts['04_removable_cover']
    scene.add_geometry(mesh(cover.translate((0, 0, 110)), color),
                       node_name='Cover (lifted)', geom_name='Cover (lifted)')
    # glTF uses metres with +Y up.
    scene.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, (1, 0, 0)))
    scene.apply_scale(0.001)
    (OUT / '05_assembly.glb').write_bytes(scene.export(file_type='glb'))
    print('STL watertightness, enclosure clearances and four mount-slot travel limits verified.')


if __name__ == '__main__':
    main()
