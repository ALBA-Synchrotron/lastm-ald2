import time
import json

import tango
import taurus

from sardana.macroserver.macro import Macro, Type, Hookable
from sardana.taurus.core.tango.sardana.macroserver import registerExtensions


__author__ = "zreszela@cells.es"
__docformat__ = "restructuredtext"
__all__ = ("ald_set_conf", "ald_get_conf", "ald_run")


registerExtensions()


class ald_set_conf(Macro):
    """Set path to ALD configuration file (must be accessible on the host
    where MacroServer is running)."""

    env = ("ALDTGCtrl",)

    param_def = [["file", Type.Filename, "/home/operatorstm/ald_seq_conf.py",
                  "Full path to configuration file"]]

    def run(self, file_):
        ctrl_name = self.getEnv("ALDTGCtrl")
        ctrl = self.getController(ctrl_name)
        ctrl.getAttribute("ConfigurationFile").write(file_)


class ald_get_conf(Macro):
    """Get path to ALD configuration file"""

    env = ("ALDTGCtrl",)

    result_def = [["file", Type.Filename, None, "Full path to configuration "
                                                "file"]]

    def run(self):
        ctrl_name = self.getEnv("ALDTGCtrl")
        ctrl = self.getController(ctrl_name)
        return ctrl.getAttribute("ConfigurationFile").read().value


class _post_cycle_remote_job(Macro):
    """Execute post-cycle job at the remote Door e.g. beamline.

    This macro is intended to be attached to the ald_run macro
    at the post-cycle hook place.
    """

    env= ("RemoteDoor", )

    def run(self):
        remote_door = self.getEnv("RemoteDoor")
        self.debug("Executing ald_post_cycle_job on {}".format(remote_door))
        remote_door = taurus.Device(remote_door)
        cycle_nb = self.parent_macro.cycle_nb
        info = json.dumps({"cycle_nb": cycle_nb})
        remote_door.runMacro("ald_post_cycle_job", [info], synch=True)


class ald_run(Macro, Hookable):
    """Execute ALD according to configuration file set with ald_set_conf
    macro"""

    env = ("ALDMeasGrp",)
    hints = {"allowsHooks": ("post-cycle", )}
    param_def = [["repeats", Type.Integer, 1, "Number of repetitions"],
                 ["wait_time", Type.Float, 0, "Wait time (s) between "
                                              "repetitions"]]

    def run(self, repeats, wait_time):
        meas_grp_name = self.getEnv("ALDMeasGrp")
        meas_grp = self.getObj(
            meas_grp_name, type_class=Type.MeasurementGroup)
        elements = meas_grp.ElementList
        tgs = []
        for elem in elements:
            obj = self.getObj(elem, type_class=Type.TriggerGate)
            if obj is not None:
                tgs.append(obj)
        conf_file = self.execMacro("ald_get_conf").getResult()
        self.info("Configuration: %s" % conf_file)
        alarm = False
        for i in range(repeats):
            self.cycle_nb = i
            self.info("Running %d repetition" % (i + 1))
            meas_grp.count(0.001)
            for hook in self.getHooks("post-cycle"):
                hook()
            time.sleep(wait_time)
            for tg in tgs:
                if tg.State() != tango.DevState.ON:
                    alarm = True
                    break
            if alarm:
                break
        if alarm:
            for tg in tgs:
                name = tg.name
                state = tg.State()
                status = tg.Status()
                if state != tango.DevState.ON:
                    log = self.warning
                else:
                    log = self.output
                log("{0} state: {1}; status: {2}".format(name, state, status))
            raise RuntimeError("ald sequence failed")
        else:
            self.info("Done")


class ald_init(Macro):
    """Initialize ALD hardware.

    This includes:
    - set RPi GPIO output pins
    """

    env = ("ALDTGCtrl",)
    VACUUM = [13, 15, 16] #vacuum valves
    def run(self):
        ctrl_name = self.getEnv("ALDTGCtrl")
        ctrl = self.getController(ctrl_name)
        axes = ctrl.getUsedAxes()
        ctrl_proxy = ctrl.getDeviceProxy()
        raspi_name = ctrl_proxy.get_property("device")["device"][0]
        raspi_proxy = tango.DeviceProxy(raspi_name)
        axes.extend(self.VACUUM) # vacuum valves 
        # TODO: add support for ozonizer using pin 32 (if possible)
        # See https://jira.cells.es/browse/CSGSW-4418
        for axis in axes:
            self.output("Setting PIN %d to output" % axis)
            raspi_proxy.write_attribute("pin%d_output" % axis, True)
            if axis in self.VACUUM:
                continue
            time.sleep(0.01)
            self.output('Setting PIN %d voltage to False', axis)
            raspi_proxy.write_attribute('pin%d_voltage' %axis, False)

