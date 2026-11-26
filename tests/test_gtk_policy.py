from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib
from queue import Queue
import sys
from threading import Event
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from asus_power.combinations import CombinationPolicy, defaults
from asus_power.config import Policy
from asus_power.ui.drafts import CombinationDraft


def controller_types():
    # 只替换不可用的 GI 绑定，执行真实 Controller.update / Editor.save。
    gi = ModuleType('gi')
    gi.require_version = lambda *_: None
    gi.require_foreign = lambda *_: None
    repository = ModuleType('gi.repository')
    for name in ('Adw', 'Gdk', 'Gio', 'GLib', 'Gtk'):
        setattr(repository, name, SimpleNamespace())
    repository.GLib.SOURCE_REMOVE = False
    modules = {'gi': gi, 'gi.repository': repository}
    # Keep fake GI imports scoped to this fixture, including all split views.
    with patch.dict(sys.modules, modules):
        module = importlib.import_module('asus_power.ui.controller')
        editor = importlib.import_module('asus_power.ui.combination_editor')
    return module.Controller, editor.CombinationEditor


class GtkPolicyTests(unittest.TestCase):
    def setUp(self):
        self.Controller, self.Editor = controller_types()
        self.controller = self.Controller.__new__(self.Controller)
        self.settings = defaults()
        self.settings['enabled'] = True
        self.settings['entries']['balanced']['ac']['power'] = {'ppt_pl1_spl': 40}
        c = self.controller
        c.combinations = self.settings
        c.combination_policy = CombinationPolicy()
        c.policy = Policy()
        c.settings = {}
        c.backend = SimpleNamespace(apply_combination=Mock(), modern=True)
        c.overview = SimpleNamespace(refresh=Mock())
        c.battery = SimpleNamespace(refresh=Mock())
        c.performance = SimpleNamespace(refresh=Mock())
        c.mode_keys = ['quiet', 'balanced', 'performance']
        c.persist = Mock(return_value=True)
        self.state = {'ac': True, 'profile': 'balanced', 'battery': None, 'limit': None,
                      'battery_watts': None, 'sensors': {}}
        self.release = Event()
        self.started = Event()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.finish)

        def block():
            self.started.set()
            if not self.release.wait(5):
                raise TimeoutError('test blocker was not released')

        self.blocker = self.pool.submit(block)
        self.assertTrue(self.started.wait(2))
        self.pending = []

        def submit(operation, *_, **__):
            self.pending.append(self.pool.submit(operation))

        c.submit = submit

    def finish(self):
        self.release.set()
        self.pool.shutdown(wait=True)

    def save(self, settings):
        editor = SimpleNamespace(controller=self.controller, collect=lambda: None,
                                 model=CombinationDraft(settings), note=Mock())
        self.Editor.save(editor)
        self.controller.persist.assert_called()

    def run_queued(self):
        self.release.set()
        self.blocker.result(timeout=2)
        return [future.result(timeout=2) for future in self.pending]

    def test_disabled_saved_policy_cancels_queued_hardware_work(self):
        self.controller.update(self.state)
        disabled = deepcopy(self.settings)
        disabled['enabled'] = False
        self.save(disabled)
        self.assertEqual(self.run_queued(), [None])
        self.controller.backend.apply_combination.assert_not_called()

    def test_edited_policy_applies_only_the_new_entry(self):
        self.controller.update(self.state)
        updated = deepcopy(self.settings)
        updated['entries']['balanced']['ac']['power']['ppt_pl1_spl'] = 50
        self.save(updated)
        self.controller.update(self.state)
        self.run_queued()
        self.controller.backend.apply_combination.assert_called_once_with(
            'balanced', 'ac', {'power': {'ppt_pl1_spl': 50}, 'fans': {}})

    def test_identical_save_still_invalidates_an_older_request(self):
        self.controller.update(self.state)
        self.save(deepcopy(self.settings))
        self.assertEqual(self.run_queued(), [None])
        self.controller.backend.apply_combination.assert_not_called()

    def test_disable_then_reenable_does_not_revive_old_work(self):
        self.controller.update(self.state)
        disabled = deepcopy(self.settings)
        disabled['enabled'] = False
        self.save(disabled)
        self.save(deepcopy(self.settings))
        self.assertEqual(self.run_queued(), [None])
        self.controller.backend.apply_combination.assert_not_called()

    def test_unchanged_pending_request_applies_once(self):
        self.controller.update(self.state)
        self.controller.update(self.state)
        self.assertEqual(len(self.pending), 1)
        self.run_queued()
        self.controller.backend.apply_combination.assert_called_once_with(
            'balanced', 'ac', {'power': {'ppt_pl1_spl': 40}, 'fans': {}})

    def test_failed_save_keeps_current_pending_request(self):
        self.controller.update(self.state)
        self.controller.persist.return_value = False
        disabled = deepcopy(self.settings)
        disabled['enabled'] = False
        self.save(disabled)
        self.run_queued()
        self.controller.backend.apply_combination.assert_called_once()


class GtkCompletionTests(unittest.TestCase):
    def setUp(self):
        Controller, _ = controller_types()
        self.controller = Controller.__new__(Controller)
        self.controller.pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.controller.pool.shutdown, wait=True)
        self.controller.closing = False
        self.controller.status = Mock(spec=['set_label'])
        self.callbacks = Queue()
        glib = Controller.submit.__globals__['GLib']
        patched = patch.object(glib, 'idle_add', self.callbacks.put, create=True)
        patched.start()
        self.addCleanup(patched.stop)

    def receive(self):
        # Worker completion is delivered to the GTK thread explicitly.
        callback = self.callbacks.get(timeout=2)
        callback()

    def test_failure_updates_both_notices_and_reenables_control(self):
        notice = Mock(spec=['set_label'])
        control = Mock(spec=['set_sensitive'])
        finished = Mock()

        def fail():
            raise RuntimeError('D-Bus denied')

        self.controller.perform(fail, control=control, notice=notice,
                                pending='正在应用组合…', finished=finished)
        control.set_sensitive.assert_called_once_with(False)
        notice.set_label.assert_called_once_with('正在应用组合…')
        self.receive()
        notice.set_label.assert_called_with('失败：D-Bus denied')
        self.controller.status.set_label.assert_called_once_with('失败：D-Bus denied')
        self.assertEqual([call.args[0] for call in control.set_sensitive.call_args_list], [False, True])
        finished.assert_called_once_with()

    def test_success_callback_error_still_finishes_operation(self):
        control = Mock(spec=['set_sensitive'])
        finished = Mock()
        done = Mock(side_effect=ValueError('callback failed'))
        self.controller.perform(lambda: {'actual': 40}, control=control,
                                done=done, finished=finished)
        self.receive()
        done.assert_called_once_with({'actual': 40})
        self.controller.status.set_label.assert_called_once_with('失败：callback failed')
        control.set_sensitive.assert_called_with(True)
        finished.assert_called_once_with()

    def test_closed_window_receives_no_queued_completion(self):
        done, finished = Mock(), Mock()
        self.controller.submit(lambda: 'saved', done, finished)
        self.controller.closing = True
        self.receive()
        done.assert_not_called()
        finished.assert_not_called()
        self.controller.status.set_label.assert_not_called()
