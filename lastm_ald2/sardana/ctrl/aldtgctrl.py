import imp
import sys
import threading

import PyTango

from sardana import State
from sardana.sardanaevent import EventReceiver
from sardana.util.funcgenerator import FunctionGenerator
from sardana.pool.controller import TriggerGateController, Type, \
    Description, Access, DataAccess
from sardana.pool.pooldefs import SynchDomain


__author__ = "zreszela@cells.es"
__docformat__ = "restructuredtext"
__all__ = ("ALDTGCtrl", "ALDTangoTGCtrl")


axis2valve = {
    3: "valve1",
    5: "valve2",
    7: "valve3",
    11: "valve4",
    12: "valve5",
    13: "valve6",
    15: "valve7",
    16: "valve8"
}

class RasPiDOCallback(EventReceiver):

    def __init__(self, device, axis):
        EventReceiver.__init__(self)
        self.device = device
        self.attr_name = "Pin%d_voltage" % axis
        self.axis = axis

    def event_received(self, src, type_, value):
        if type_.name == "active":
            voltage = True
        elif type_.name == "passive":
            voltage = False
        else:
            return
        self.device.setvoltage(self.axis, voltage)


class ALDTGCtrl(TriggerGateController):
    """Basic controller intended for demonstration purposes only.
    """
    organization = "ALBA-Cells"
    MaxDevice = 1

    class_prop = {
        "Device": {Type: str,
                   Description: "Raspberry PI device"}
    }

    def __init__(self, inst, props, *args, **kwargs):
        """Constructor"""
        TriggerGateController.__init__(self, inst, props, *args, **kwargs)
        from raspberry_pi.RPi import Raspberry
        self.device = Raspberry("raspi030")
        self.device.connect_to_pi()
        self.tg = {}
        self.cbs = {}
        self.conf = {}

    def AddDevice(self, axis):
        self._log.debug('AddDevice(%d): entering...' % axis)
        idx = axis - 1
        self.device.setoutput(axis, True)
        func_generator = FunctionGenerator()
        func_generator.initial_domain = SynchDomain.Time
        func_generator.active_domain = SynchDomain.Time
        cb = RasPiDOCallback(self.device, axis)
        func_generator.add_listener(cb)
        self.tg[idx] = func_generator
        self.cbs[idx] = cb
        self._log.debug('AddDevice(%d): leaving...' % axis)

    def SynchOne(self, axis, conf):
        idx = axis - 1
        tg = self.tg[idx]
        tg.set_configuration(conf)
        self.conf[idx] = conf

    def StateOne(self, axis):
        """Get the dummy trigger/gate state"""
        self._log.debug('StateOne(%d): entering...' % axis)
        sta = State.On
        status = "Stopped"
        idx = axis - 1
        tg = self.tg[idx]
        if tg.is_running() or tg.is_started():
            sta = State.Moving
            status = "Moving"
        self._log.debug('StateOne(%d): returning (%s, %s)' %
                        (axis, sta, status))
        return sta, status

    def PreStartAll(self):
        pass

    def StartAll(self):
        pass

    def PreStartOne(self, axis, value=None):
        return True

    def StartOne(self, axis):
        """Start the specified trigger
        """
        self._log.debug('StartOne(%d): entering...' % axis)
        idx = axis - 1
        tg = self.tg[idx]
        tg.start()
        self.thread = threading.Thread(target=tg.run)
        self.thread.start()

    def AbortOne(self, axis):
        """Start the specified trigger
        """
        self._log.debug('AbortOne(%d): entering...' % axis)
        idx = axis - 1
        self.tg[idx].stop()


class RasPiTangoDOCallback(EventReceiver):

    def __init__(self, ctrl, axis):
        EventReceiver.__init__(self)
        self.ctrl = ctrl
        self.axis = axis

    def event_received(self, src, type_, value):
        attr_name = "Pin%d_voltage" % self.axis
        if type_.name == "active":
            voltage = True
            pairs = [(attr_name, voltage)]
            if self.axis == 3:
                pairs.append(("Pin5_voltage", False))
            elif self.axis == 5:
                pairs.append(("Pin3_voltage", True))
        elif type_.name == "passive":
            if self.axis == 5:
                pairs = [("Pin3_voltage", False),
                         ("Pin5_voltage", False)]
            else:
                voltage = False
                pairs = [(attr_name, voltage)]
        else:
            return
        try:
            self.ctrl.device.write_attributes(pairs)
        except Exception as e:
            self.ctrl._log.error("Exception while handling callback:", exc_info=True)
            self.ctrl._log.debug("Stopping generation...")
            idx = self.axis - 1
            self.ctrl.tg[idx].stop()
            self.ctrl.state[idx] = State.Alarm
            status = "Setting "
            attr, voltage = pairs[0]
            status += "{0} to {1}".format(attr, voltage)
            try:
                attr, voltage = pairs[1]
                status += " and {0} to {1}".format(attr, voltage)
            except IndexError:
                pass
            status += " failed. Check Pool logs for more details."
            self.ctrl.status[idx] = status


class ALDTangoTGCtrl(TriggerGateController):
    """Basic controller intended for demonstration purposes only.
    """
    organization = "ALBA-Cells"
    MaxDevice = 1

    class_prop = {
        "Device": {Type: str,
                   Description: "Raspberry PI device"}
    }

    ctrl_attributes = {
        "ConfigurationFile": {
                Type: str,
                Description: "Full path to configuration file",
                Access: DataAccess.ReadWrite,
        },
    }

    def __init__(self, inst, props, *args, **kwargs):
        """Constructor"""
        TriggerGateController.__init__(self, inst, props, *args, **kwargs)
        self.device = PyTango.DeviceProxy(self.Device)
        self.tg = {}
        self.cbs = {}
        self.state = {}
        self.status = {}
        self.conf = {}

    def AddDevice(self, axis):
        self._log.debug('AddDevice(%d): entering...' % axis)
        idx = axis - 1
        func_generator = FunctionGenerator()
        func_generator.initial_domain = SynchDomain.Time
        func_generator.active_domain = SynchDomain.Time
        cb = RasPiTangoDOCallback(self, axis)
        func_generator.add_listener(cb)
        self.tg[idx] = func_generator
        self.cbs[idx] = cb
        self.state[idx] = State.On
        self.status[idx] = "Stopped"
        self._log.debug("AddDevice(%d): leaving..." % axis)

    def SynchOne(self, axis, _):
        """Ignore configuration coming from Sardana and use configuration
        set by user in ConfigurationFile"""
        idx = axis - 1
        tg = self.tg[idx]
        # due to sardana-org/sardana#787 use lowercase
        if not hasattr(self, "_configurationfile"):
            raise RuntimeError("controller's ConfigurationFile is not set")
        # remove configuration module from sys.modules in order to
        # ensure a correct reload
        if "ald_sequence_config" in sys.modules:
            sys.modules.pop("ald_sequence_config")
        ald_sequence_config = imp.load_source("ald_sequence_config",
                                              self._configurationfile)
        valve = axis2valve[axis]
        try:
            attr_name = "%s_conf" % valve
            conf = getattr(ald_sequence_config, attr_name)
        except AttributeError:
            msg = "%s is missing in configuration file %s" % (attr_name,
                  self._configurationfile)
            raise RuntimeError(msg)
        tg.set_configuration(conf)
        self.conf[idx] = conf

    def StateOne(self, axis):
        """Get the dummy trigger/gate state"""
        self._log.debug("StateOne(%d): entering..." % axis)
        idx = axis - 1
        sta = self.state[idx]
        status = self.status[idx]
        tg = self.tg[idx]
        if tg.is_running() or tg.is_started():
            sta = State.Moving
            status = "Moving"
        self._log.debug("StateOne(%d): returning (%s, %s)" %
                        (axis, sta, status))
        return sta, status

    def PreStartAll(self):
        pass

    def StartAll(self):
        pass

    def PreStartOne(self, axis, value=None):
        idx = axis - 1
        self.state[idx] = State.On
        self.status[idx] = "Stopped"
        return True

    def StartOne(self, axis):
        """Start the specified trigger
        """
        self._log.debug("StartOne(%d): entering..." % axis)
        idx = axis - 1
        tg = self.tg[idx]
        tg.start()
        self.thread = threading.Thread(target=tg.run)
        self.thread.start()

    def AbortOne(self, axis):
        """Start the specified trigger
        """
        self._log.debug("AbortOne(%d): entering..." % axis)
        idx = axis - 1
        self.tg[idx].stop()
