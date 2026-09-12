// The picture: the kernel's faces as models under a z-up node, lit by a sky, picked by name;
// the marks on the pick, the badge beside the cursor, the picked feature's numbers.
import QtQuick
import QtQuick3D
import QtQuick3D.Helpers

Item {
    id: root

    // CAD (z up) to the scene (y up): (x, y, z) -> (x, z, -y)
    function scenePoint(x, y, z) { return Qt.vector3d(x, z, -y) }
    function pickAt(x, y) {
        const hit = view.pick(x, y)
        return hit.objectHit ? hit.objectHit.objectName : ""
    }
    function hitAt(x, y) {
        const hit = view.pick(x, y)
        if (!hit.objectHit) return []
        const p = hit.scenePosition
        return [hit.objectHit.objectName, p.x, p.y, p.z]
    }
    function project(x, y, z) { return view.mapFrom3DScene(scenePoint(x, y, z)) }
    function mmPerPixel(x, y, z) {
        const p = scenePoint(x, y, z), r = camera.right
        const a = view.mapFrom3DScene(p), b = view.mapFrom3DScene(Qt.vector3d(p.x + r.x, p.y + r.y, p.z + r.z))
        const d = Math.hypot(b.x - a.x, b.y - a.y)
        return d > 1e-9 ? 1 / d : 0
    }

    View3D {
        id: view
        anchors.fill: parent
        camera: camera                  // mapFrom3DScene answers nothing until the camera is set here
        environment: ExtendedSceneEnvironment {
            backgroundMode: SceneEnvironment.Color
            clearColor: "#14171e"
            antialiasingMode: SceneEnvironment.MSAA
            antialiasingQuality: SceneEnvironment.High
            aoEnabled: true
            aoStrength: 50
            aoDistance: 6
            aoSoftness: 40
            tonemapMode: SceneEnvironment.TonemapModeFilmic
            probeExposure: 0.45
            lightProbe: Texture { textureData: ProceduralSkyTextureData { sunEnergy: 0.35; skyTopColor: "#6f8fc7"; skyHorizonColor: "#c9d3e2"; groundBottomColor: "#2a2e38"; groundHorizonColor: "#4a505c" } }
        }

        // the camera orbits a point: yaw about the world's up, pitch about the view's right
        Node {
            id: orbit
            objectName: "orbit"
            position: bridge.centre
            eulerRotation: Qt.vector3d(-24, 35, 0)
            PerspectiveCamera {
                id: camera
                position: Qt.vector3d(0, 0, bridge.distance)
                clipNear: Math.max(0.1, bridge.distance * 0.01)
                clipFar: bridge.distance * 400
                fieldOfView: 34
            }
        }
        DirectionalLight {
            eulerRotation: Qt.vector3d(-48, 28, 0)
            brightness: 0.8
            castsShadow: true
            shadowMapQuality: Light.ShadowMapQualityHigh
            shadowFactor: 55
            shadowBias: 8
            softShadowQuality: Light.PCF8
        }
        DirectionalLight { eulerRotation: Qt.vector3d(-20, -140, 0); brightness: 0.15 }

        Node {
            id: cad
            eulerRotation.x: -90            // the kernel is z-up; Qt Quick 3D is y-up
            Repeater3D {
                model: bridge.parts
                delegate: Node {
                    required property var modelData
                    position: modelData.position
                    rotation: modelData.rotation
                    Repeater3D {
                        model: modelData.faces
                        delegate: Model {
                            required property var modelData
                            geometry: modelData.geometry
                            objectName: modelData.name
                            pickable: true
                            materials: PrincipledMaterial {
                                baseColor: modelData.picked ? "#7aa2f7" : modelData.shade
                                roughness: 0.62
                                metalness: 0.04
                                cullMode: Material.NoCulling
                            }
                        }
                    }
                    Model {
                        geometry: modelData.lines
                        pickable: false
                        materials: PrincipledMaterial {
                            lighting: PrincipledMaterial.NoLighting
                            baseColor: modelData.faces.length ? "#0b0d12" : "#7aa2f7"
                            lineWidth: modelData.faces.length ? 1 : 2
                        }
                    }
                }
            }
            // the marks on the pick: the arrow and the plane square as lines, the tips as models
            Model {
                geometry: bridge.markLines
                pickable: false
                materials: PrincipledMaterial { lighting: PrincipledMaterial.NoLighting; baseColor: "#f2c14e"; lineWidth: 2 }
            }
            Model {
                geometry: bridge.pickedLines
                pickable: false
                materials: PrincipledMaterial { lighting: PrincipledMaterial.NoLighting; baseColor: "#7aa2f7"; lineWidth: 3 }
            }
            Model {
                geometry: bridge.ring
                pickable: false
                materials: PrincipledMaterial { lighting: PrincipledMaterial.NoLighting; baseColor: "#f2c14e"; lineWidth: 2 }
            }
            Repeater3D {
                model: bridge.marks
                delegate: Model {
                    required property var modelData
                    position: modelData.at
                    objectName: "mark:" + modelData.name
                    pickable: true
                    source: modelData.kind === "box" ? "#Cube" : modelData.kind === "disc" ? "#Cylinder"
                          : modelData.kind === "corner" ? "#Cone" : "#Sphere"
                    // the built-in meshes are 100 units across: scale to the mark's radius
                    scale: modelData.kind === "disc" ? Qt.vector3d(modelData.radius / 45, modelData.radius / 140, modelData.radius / 45)
                         : modelData.kind === "box" ? Qt.vector3d(modelData.radius / 60, modelData.radius / 60, modelData.radius / 300)
                         : Qt.vector3d(modelData.radius / 50, modelData.radius / 50, modelData.radius / 50)
                    materials: PrincipledMaterial { baseColor: modelData.colour; emissiveFactor: Qt.vector3d(0.35, 0.3, 0.1); roughness: 0.5 }
                }
            }
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: bridge.tool !== "" || bridge.following !== ""
        acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
        property real lastX: 0
        property real lastY: 0
        property real downX: 0
        property real downY: 0
        property bool moved: false
        property string dragging: ""
        property bool endedDrag: false      // the press that ends a followed drag is not a pick
        onPressed: (e) => {
            lastX = downX = e.x; lastY = downY = e.y; moved = false
            if (bridge.following !== "") { bridge.dragEnd(); endedDrag = true; return }
            if (bridge.tool !== "") return
            if (e.button === Qt.LeftButton) {
                const name = root.pickAt(e.x, e.y)
                if (name.startsWith("mark:")) { dragging = name.slice(5); bridge.dragStart(dragging, e.x, e.y) }
            }
        }
        onPositionChanged: (e) => {
            if (bridge.following !== "" || dragging !== "") { bridge.dragMove(e.x, e.y, (e.modifiers & Qt.ControlModifier) !== 0); return }
            if (bridge.tool !== "" && !(e.buttons)) { bridge.hover(e.x, e.y, false); return }
            const dx = e.x - lastX, dy = e.y - lastY
            if (Math.abs(e.x - downX) + Math.abs(e.y - downY) > 3) moved = true
            if (!moved) return
            if (e.buttons & Qt.LeftButton && !(e.modifiers & Qt.ShiftModifier)) {
                orbit.eulerRotation.y -= dx * 0.4
                orbit.eulerRotation.x = Math.max(-89, Math.min(89, orbit.eulerRotation.x - dy * 0.4))
            } else {
                const k = bridge.distance * 0.0016
                const right = orbit.right, up = orbit.up
                bridge.centre = Qt.vector3d(bridge.centre.x - right.x * dx * k + up.x * dy * k,
                                            bridge.centre.y - right.y * dx * k + up.y * dy * k,
                                            bridge.centre.z - right.z * dx * k + up.z * dy * k)
            }
            lastX = e.x; lastY = e.y
        }
        onReleased: (e) => {
            if (dragging !== "") { dragging = ""; bridge.dragEnd(); return }
            if (endedDrag) { endedDrag = false; return }
            if (moved) return
            const name = root.pickAt(e.x, e.y)
            if (e.button === Qt.RightButton) bridge.menu(name, e.x, e.y)
            else if (bridge.tool !== "") bridge.hover(e.x, e.y, true) // the tool's click: Python reads the hover
            else bridge.pick(name.startsWith("mark:") ? "" : name, e.x, e.y, (e.modifiers & Qt.ShiftModifier) !== 0)
        }
        onWheel: (e) => {
            if (bridge.tool !== "") { bridge.wheel(e.angleDelta.y); return }
            bridge.distance = bridge.distance * Math.exp(-e.angleDelta.y * 0.0015)
        }
    }

    // the picked feature's numbers, beside the faces it made; a click types over one
    Column {
        property var at: view.camera ? view.mapFrom3DScene(bridge.labelAt) : Qt.vector3d(0, 0, 0)
        x: at.x + 22
        y: at.y - 10
        spacing: 3
        visible: bridge.labels.length > 0 && bridge.badge === ""
        Repeater {
            model: bridge.labels
            delegate: Rectangle {
                required property var modelData
                width: labelText.width + 18; height: 22; radius: 8; color: "#f2c14e"
                Text { id: labelText; anchors.centerIn: parent; text: modelData.text; color: "#1a1400"; font.pixelSize: 12; font.bold: true }
                MouseArea { anchors.fill: parent; cursorShape: Qt.IBeamCursor; onClicked: bridge.labelClick(modelData.key) }
            }
        }
    }
    // the badge: what a drag is at, beside the cursor
    Rectangle {
        visible: bridge.badge !== ""
        x: mouse.mouseX + 18; y: mouse.mouseY + 18
        width: badgeText.width + 18; height: 24; radius: 8
        color: bridge.badgeRefused ? "#ff7b7b" : "#f2c14e"
        Text { id: badgeText; anchors.centerIn: parent; text: bridge.badge; color: "#1a1400"; font.pixelSize: 12; font.bold: true }
    }
}
