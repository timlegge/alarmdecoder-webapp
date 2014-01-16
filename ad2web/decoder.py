# -*- coding: utf-8 -*-

from gevent import monkey
monkey.patch_all()

import time

from socketio import socketio_manage
from socketio.namespace import BaseNamespace
from socketio.mixins import BroadcastMixin
from socketio.server import SocketIOServer

from flask import Blueprint, Response, request, g, current_app
import jsonpickle

from alarmdecoder import AlarmDecoder
from alarmdecoder.devices import SocketDevice

from .log.models import EventLogEntry
from .log.constants import *
from .extensions import db


CRITICAL_EVENTS = [POWER_CHANGED, ALARM, BYPASS, ARM, DISARM, ZONE_FAULT, \
                    ZONE_RESTORE, FIRE, PANIC]

EVENTS = {
    ARM: 'on_arm',
    DISARM: 'on_disarm',
    POWER_CHANGED: 'on_power_changed',
    ALARM: 'on_alarm',
    FIRE: 'on_fire',
    BYPASS: 'on_bypass',
    BOOT: 'on_boot',
    CONFIG_RECEIVED: 'on_config_received',
    ZONE_FAULT: 'on_zone_fault',
    ZONE_RESTORE: 'on_zone_restore',
    LOW_BATTERY: 'on_low_battery',
    PANIC: 'on_panic',
    RELAY_CHANGED: 'on_relay_changed'
}

EVENT_MESSAGES = {
    ARM: 'The alarm was armed.',
    DISARM: 'The alarm was disarmed.',
    POWER_CHANGED: 'Power status has changed.',
    ALARM: 'Alarming!  Oh no!',
    FIRE: 'Fire!  Oh no!',
    BYPASS: 'A zone has been bypassed.',
    BOOT: 'The AlarmDecoder has finished booting.',
    CONFIG_RECEIVED: 'AlarmDecoder has been configuratorized.',
    ZONE_FAULT: 'A zone has been faulted.',
    ZONE_RESTORE: 'A zone has been restored.',
    LOW_BATTERY: 'Low battery detected.  You should probably mount it higher.',
    PANIC: 'Panic!  Ants are invading the pantry!',
    RELAY_CHANGED: 'Some relay or another has changed.'
}

decodersocket = Blueprint('sock', __name__, url_prefix='/socket.io')

def create_decoder_socket(app):
    return SocketIOServer(('', 5000), app, resource="socket.io")

class Decoder(object):
    def __init__(self, app, websocket):
        self.app = app
        self.device = AlarmDecoder(SocketDevice(interface=('localhost', 10000)))
        self.websocket = websocket

        self._last_message = None

    def open(self):
        self.bind_events(self.websocket, self.device)
        self.device.open(baudrate=115200)

    def close(self):
        self.device.close()

    def bind_events(self, appsocket, decoder):
        build_event_handler = lambda ftype: lambda sender, *args, **kwargs: self._handle_event(ftype, sender, *args, **kwargs)

        self.device.on_message += self._on_message
        self.device.on_lrr_message += self._on_message
        self.device.on_rfx_message += self._on_message
        self.device.on_expander_message += self._on_message

        # Bind the event handler to all of our events.
        for event, device_event_name in EVENTS.iteritems():
            device_handler = getattr(self.device, device_event_name)
            device_handler += build_event_handler(event)

    def _on_message(self, sender, *args, **kwargs):
        try:
            message = kwargs.get('message', None)
            packet = self._make_packet('message', jsonpickle.encode(message, unpicklable=False))

            self._broadcast_packet(packet)

        except Exception, err:
            import traceback
            traceback.print_exc(err)

    def _handle_event(self, ftype, sender, *args, **kwargs):
        try:
            #print ftype, sender, args, kwargs
            self._last_message = time.time()

            if ftype in CRITICAL_EVENTS:
                print 'critical event!', ftype, kwargs

            with self.app.app_context():
                db.session.add(EventLogEntry(type=ftype, message=EVENT_MESSAGES[ftype]))
                db.session.commit()

            message = jsonpickle.encode(kwargs, unpicklable=False)
            packet = self._make_packet('event', message)

            self._broadcast_packet(packet)

        except Exception, err:
            import traceback
            traceback.print_exc(err)

    def _broadcast_packet(self, packet):
        for session, sock in self.websocket.sockets.iteritems():
            sock.send_packet(packet)

    def _make_packet(self, channel, data):
        return dict(type='event', name=channel, args=data, endpoint='/alarmdecoder')


decoder = Decoder(None, None)

class DecoderNamespace(BaseNamespace, BroadcastMixin):
    def initialize(self):
        self._alarmdecoder = self.request

    def on_keypress(self, key):
        print 'sending keypress: {0}'.format(key)
        self._alarmdecoder.device.send(key)

@decodersocket.route('/<path:remaining>')
def handle_socketio(remaining):
    try:
        socketio_manage(request.environ, {'/alarmdecoder': DecoderNamespace}, g.alarmdecoder)

    except Exception, err:
        from flask import current_app
        current_app.logger.error("Exception while handling socketio connection", exc_info=True)

    return Response()