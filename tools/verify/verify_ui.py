"""Verify discovery, selection actions, and editing the result in Blender."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

bpy.ops.preferences.addon_enable(module="cadcore_bridge")
import cadcore_bridge as addon


class Layout:
    def __init__(self):
        self.actions = []

    def row(self, **kwargs):
        return self

    column = box = row

    def operator(self, name, **kwargs):
        group, operator = name.split('.')
        getattr(getattr(bpy.ops, group), operator).get_rna_type()
        result = SimpleNamespace()
        self.actions.append((name, result))
        return result

    def menu(self, name, **kwargs):
        assert any(cls.__name__ == name for cls in addon.CLASSES), name

    def prop(self, item, name, **kwargs):
        assert hasattr(item, name), name

    def operator_menu_enum(self, name, prop, **kwargs):
        self.operator(name, **kwargs)

    def label(self, **kwargs):
        pass

    def separator(self):
        pass

    def template_list(self, *args, **kwargs):
        pass

    template_icon_view = template_list


def draw_all(context):
    for cls in addon.CLASSES:
        if issubclass(cls, bpy.types.Panel) and getattr(cls, "poll", lambda c: True)(context):
            cls.draw(SimpleNamespace(layout=Layout()), context)
        elif issubclass(cls, bpy.types.Menu):
            cls.draw(SimpleNamespace(layout=Layout()), context)


context = bpy.context
props = context.scene.cadcore
area = next(a for a in context.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
with context.temp_override(area=area, region=region):
    draw_all(context)
    assert bpy.ops.cadcore.new_document() == {'FINISHED'}
    assert bpy.ops.cadcore.start(shape='box', size_x=80, size_y=60, size_z=10) == {'FINISHED'}
    draw_all(context)
    addon.sync.select_faces([])
    layout = Layout()
    addon.panels.CADCORE_PT_tools.draw(SimpleNamespace(layout=layout), context)
    assert {p.action for name, p in layout.actions if name == 'cadcore.begin_face_action'} == {'hole', 'pocket'}

    faces = addon.state.get_client(context).call('describe_faces')['faces']
    top = next(f['name'] for f in faces if f.get('normal', [0, 0, 0])[2] > .9)
    dispatched = []
    def dispatch(ctx):
        dispatched.append(addon.sync.selected_face_names())
        return {'FINISHED'}
    guide = SimpleNamespace(action='hole', area=area, region=region, execute=dispatch)
    guide_type = addon.actions.CADCORE_OT_begin_face_action
    guide.cancel = lambda ctx: guide_type.cancel(guide, ctx)
    click = SimpleNamespace(type='LEFTMOUSE', value='PRESS',
                            mouse_x=region.x + 10, mouse_y=region.y + 10)
    with patch('cadcore_bridge.viewport.pick._face_at', return_value=(None, None, None)):
        assert guide_type.modal(guide, context, click) == {'RUNNING_MODAL'}
        assert not dispatched
    with patch('cadcore_bridge.viewport.pick._face_at', return_value=(top, None, None)):
        assert guide_type.modal(guide, context, click) == {'FINISHED'}
        assert dispatched == [[top]]
    assert guide_type.invoke(guide, context, click) == {'FINISHED'}
    assert dispatched == [[top], [top]]
    assert guide_type.modal(guide, context, SimpleNamespace(type='ESC', value='PRESS')) == {'CANCELLED'}
    addon.sync.select_faces([top])
    layout = Layout()
    addon.menus.draw_selection_actions(layout, context, compact=True)
    assert {p.action for name, p in layout.actions if name == 'cadcore.begin_face_action'} == {'hole', 'pocket', 'push'}
    draw_all(context)

    before = props.volume
    assert bpy.ops.cadcore.hole_face(hole_d=6, hole_standard='none', hole_depth=0, pocket_count=1) == {'FINISHED'}
    assert props.features[props.feature_index].kind == 'hole'
    assert props.volume < before
    bore = next(f['name'] for f in addon.state.get_client(context).call('describe_faces')['faces']
                if f.get('shape') == 'cylinder')
    dispatched.clear()
    with patch('cadcore_bridge.viewport.pick._face_at', return_value=(bore, None, None)):
        assert guide_type.modal(guide, context, click) == {'RUNNING_MODAL'}
        assert not dispatched
    diameter = next(a for a in props.feature_args if a.name == 'diameter')
    diameter.value = 8
    addon.throttle.flush()
    changed = props.volume
    assert changed < before
    assert bpy.ops.cadcore.undo(redo=False) == {'FINISHED'}
    assert props.volume > changed
    assert bpy.ops.cadcore.undo(redo=True) == {'FINISHED'}
    assert abs(props.volume - changed) < .001
    draw_all(context)
    assert addon.panels.CADCORE_PT_export.bl_parent_id == 'CADCORE_PT_panel'
    assert 'DEFAULT_CLOSED' not in getattr(addon.panels.CADCORE_PT_assistant, 'bl_options', set())

    out = Path(__file__).resolve().parents[2] / 'build' / 'ui-review.step'
    assert bpy.ops.cadcore.export_step(filepath=str(out)) == {'FINISHED'}
    assert out.stat().st_size > 100

addon.state.stop_client()
print('UI VERIFY RESULT all ok')
