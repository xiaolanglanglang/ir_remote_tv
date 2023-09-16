import logging
import asyncio
import os
from datetime import datetime, timedelta
import json

from homeassistant.components.media_player import (MediaPlayerEntity, PLATFORM_SCHEMA, DEVICE_CLASS_TV)
from homeassistant.components.media_player.const import (ATTR_INPUT_SOURCE, ATTR_MEDIA_VOLUME_MUTED, ATTR_MEDIA_VOLUME_LEVEL, SUPPORT_TURN_OFF, SUPPORT_TURN_ON, SUPPORT_PREVIOUS_TRACK, SUPPORT_NEXT_TRACK, SUPPORT_VOLUME_STEP, SUPPORT_VOLUME_SET, SUPPORT_VOLUME_MUTE, SUPPORT_SELECT_SOURCE)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.const import (ATTR_ENTITY_ID, CONF_NAME, STATE_OFF, STATE_ON, STATE_PLAYING)


import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

COMPONENT_ABS_DIR = os.path.dirname(
    os.path.abspath(__file__))

DOMAIN = 'ir_remote_tv'
DEFAULT_NAME = "IR Remote TV"
OPERATION_TIMEOUT = timedelta(seconds=60)

CONF_UNIQUE_ID = 'unique_id'
CONF_DEVICE_CODE = 'device_code'
CONF_REMOTE_ENTITY_ID = 'remote_entity_id'
CONF_POWER_SENSOR = 'power_sensor'
CONF_EVENT_NAME = 'event_name'
CONF_LISTEN_HOMEKIT_REMOTE = 'listen_homekit_remote'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_UNIQUE_ID): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_DEVICE_CODE): cv.positive_int,
    vol.Required(CONF_REMOTE_ENTITY_ID): cv.entity_id,
    vol.Optional(CONF_POWER_SENSOR): cv.entity_id,
    vol.Optional(CONF_POWER_SENSOR): cv.entity_id,
    vol.Optional(CONF_EVENT_NAME): cv.string,
    vol.Optional(CONF_LISTEN_HOMEKIT_REMOTE, default=False): cv.boolean,
})


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    device_code = config[CONF_DEVICE_CODE]
    device_file_name = str(device_code) + '.json'
    map_file_path = os.path.join(COMPONENT_ABS_DIR, 'codes')
    map_file = os.path.join(map_file_path, device_file_name)
    if not os.path.exists(map_file):
        _LOGGER.error("JSON file not found")
        return
    with open(map_file) as j:
        try:
            device_data = json.load(j)
        except Exception:
            _LOGGER.error("JSON file is invalid")
            return
    async_add_entities([IrRemoteTV(hass, config, device_data)])


class IrRemoteTV(MediaPlayerEntity, RestoreEntity):
    def __init__(self, hass, config, device_data):
        self.hass = hass
        self._unique_id = config.get(CONF_UNIQUE_ID)
        self._name = config.get(CONF_NAME)
        self._device_code = config.get(CONF_DEVICE_CODE)
        self._power_sensor = config.get(CONF_POWER_SENSOR)
        self._remote_entity_id = config.get(CONF_REMOTE_ENTITY_ID)
        self._event_name = config.get(CONF_EVENT_NAME)
        self._listen_homekit_event = config.get(CONF_LISTEN_HOMEKIT_REMOTE)

        self._manufacturer = device_data['manufacturer']
        self._model = device_data['model']
        self._commands = device_data['commands']
        self._switch_source = device_data['switchSoure']
        self._homekit_map = device_data['homekitMap']

        self._state = STATE_OFF
        self._sources_list = []
        self._source = None
        self._support_flags = 0
        self._volume_level = 0
        self._attr_is_volume_muted = False

        self._command_history = []
        self._temp_lock = asyncio.Lock()
        self._last_command_request_time = datetime.now()
        self._last_power_operation_time = datetime.now()

        if 'powerOn' in self._commands:
            self._support_flags = self._support_flags | SUPPORT_TURN_ON
        if 'powerOff' in self._commands:
            self._support_flags = self._support_flags | SUPPORT_TURN_OFF
        if 'volumeUp' in self._commands or 'volumeDown' in self._commands:
            self._support_flags = self._support_flags | SUPPORT_VOLUME_STEP | SUPPORT_VOLUME_SET
        if 'mute' in self._commands:
            self._support_flags = self._support_flags | SUPPORT_VOLUME_MUTE
        if 'previousChannel' in self._commands:
            self._support_flags = self._support_flags | SUPPORT_PREVIOUS_TRACK
        if 'nextChannel' in self._commands:
            self._support_flags = self._support_flags | SUPPORT_NEXT_TRACK
        if self._switch_source['type'] != 'none':
            self._support_flags = self._support_flags | SUPPORT_SELECT_SOURCE
            for source in self._switch_source['sourceList']:
                self._sources_list.append(source['name'])
            if len(self._sources_list) > 0:
                self._source = self._sources_list[0]

        if self._event_name is not None:
            hass.bus.async_listen(self._event_name, self._ir_receiver_event_handler)
        if self._listen_homekit_event:
            hass.bus.async_listen('homekit_tv_remote_key_pressed', self._homekit_event_handler)

    @property
    def supported_features(self):
        return self._support_flags

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def device_class(self):
        return DEVICE_CLASS_TV

    @property
    def state(self):
        """Return the state of the player."""
        return self._state

    @property
    def volume_level(self):
        """Return the volume level of the media player (0..1)."""
        return self._volume_level

    @property
    def source_list(self):
        return self._sources_list

    @property
    def source(self):
        if self.state == STATE_OFF:
            return None
        return self._source

    async def async_turn_off(self , execute=True):
        date = datetime.now()
        self._last_command_request_time = date
        if self._state == STATE_PLAYING and execute:
            await self.async_send_ir_command('powerOff', date)
        self._state = STATE_OFF
        self._last_power_operation_time = date
        await self.async_update_ha_state()

    async def async_turn_on(self, execute=True):
        date = datetime.now()
        self._last_command_request_time = date
        if self._state == STATE_OFF and execute:
            await self.async_send_ir_command('powerOn', date)
        self._state = STATE_PLAYING
        self._last_power_operation_time = date
        await self.async_update_ha_state()

    async def async_media_previous_track(self):
        date = datetime.now()
        await self.async_send_ir_command('previousChannel', date)
        await self.async_update_ha_state()

    async def async_media_next_track(self):
        date = datetime.now()
        await self.async_send_ir_command('nextChannel', date)
        await self.async_update_ha_state()

    async def async_volume_up(self, update_request_time=True, date=None, execute=True):
        if date is None:
            date = datetime.now()
        if update_request_time:
            self._last_command_request_time = date
        if execute:
            await self.async_send_ir_command('volumeUp', date)
        self._volume_level = min(round(self._volume_level + 0.01, 2), 1)
        self._attr_is_volume_muted = False
        self.async_write_ha_state()

    async def async_volume_down(self, update_request_time=True, date=None, execute=True):
        if date is None:
            date = datetime.now()
        if update_request_time:
            self._last_command_request_time = date
        if execute:
            await self.async_send_ir_command('volumeDown', date)
        self._volume_level = max(round(self._volume_level - 0.01, 2), 0)
        self.async_write_ha_state()

    async def async_set_volume_level(self, volume):
        date = datetime.now()
        self._last_command_request_time = date
        if volume < self._volume_level:
            step_number = round((self._volume_level - volume), 2) * 100
            command = 'volumeDown'
        elif volume > self.volume_level:
            step_number = round((volume - self._volume_level), 2) * 100
            command = 'volumeUp'
        else:
            return
        for _ in range(int(step_number)):
            if command == 'volumeUp':
                await self.async_volume_up(False, date)
            if command == 'volumeDown':
                await self.async_volume_down(False, date)

    async def async_mute_volume(self, mute, execute=True):
        date = datetime.now()
        self._last_command_request_time = date
        if execute:
            await self.async_send_ir_command('mute', date)
        self._attr_is_volume_muted = mute
        self.async_write_ha_state()

    async def async_select_source(self, source):
        """Select channel from source."""
        old_source_index = 0
        for source_info in self._switch_source['sourceList']:
            if source_info['name'] == source:
                new_source_index = source_info['index']
            if source_info['name'] == self._source:
                old_source_index = source_info['index']
        if new_source_index == old_source_index:
            return
        step = new_source_index - old_source_index
        if step > 0:
            command = self._switch_source['next']
        else:
            command = self._switch_source['previous']
        await self.async_send_ir_command('selectSource', datetime.now())
        for _ in range(abs(step)):
            await self.async_send_ir_command(command, datetime.now())
        await self.async_send_ir_command('ok', datetime.now())
        self._source = source
        await self.async_update_ha_state()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if not last_state:
            return
        self._state = last_state.state
        if ATTR_MEDIA_VOLUME_LEVEL in last_state.attributes:
            self._volume_level = last_state.attributes[ATTR_MEDIA_VOLUME_LEVEL]
        if ATTR_MEDIA_VOLUME_MUTED in last_state.attributes:
            self._attr_is_volume_muted = last_state.attributes[ATTR_MEDIA_VOLUME_MUTED]
        if ATTR_INPUT_SOURCE in last_state.attributes:
            self._source = last_state.attributes[ATTR_INPUT_SOURCE]

    @property
    def extra_state_attributes(self):
        attributes = {}
        attributes[ATTR_MEDIA_VOLUME_LEVEL] = self._volume_level
        attributes[ATTR_MEDIA_VOLUME_MUTED] = self._attr_is_volume_muted
        attributes[ATTR_INPUT_SOURCE] = self._source
        return attributes

    async def async_update(self):
        if self._event_name is not None and len(self._command_history) > 0:
            async with self._temp_lock:
                self._command_history = [x for x in self._command_history if not x.is_outdate()]
        if self._power_sensor is None:
            return
        power_state = self.hass.states.get(self._power_sensor)
        if power_state is None:
            return
        date = datetime.now()
        if date - self._last_power_operation_time < OPERATION_TIMEOUT:
            return
        if power_state.state == STATE_ON:
            self._state = STATE_PLAYING
        if power_state.state == STATE_OFF:
            self._state = STATE_OFF

    async def async_send_ir_command(self, command, time):
        """Send a command."""
        async with self._temp_lock:
            if time < self._last_command_request_time:
                return False
            try:
                raw = self._commands[command]['raw']
                if self._event_name is not None :
                    self._command_history.append(CommandHistory(command, raw, time))
                service_data = {
                    ATTR_ENTITY_ID: self._remote_entity_id,
                    'command': raw
                }
                return await self.hass.services.async_call('remote', 'send_command', service_data, blocking=True)
            except Exception as e:
                _LOGGER.exception(e)
                return False

    async def _ir_receiver_event_handler(self, event):
        print(event.data)
        data = event.data
        execute_command = None
        raw = None
        for name in self._commands:
            command = self._commands[name]
            if command['address'] != data['address'] or command['command'] != data['command']:
                continue
            execute_command = name
            raw = command['raw']
        if not execute_command:
            return
        async with self._temp_lock:
            for history in self._command_history:
                if history.is_outdate():
                    self._command_history.remove(history)
                    continue
                if history.raw() == raw:
                    _LOGGER.debug('Command %s is already in history', execute_command)
                    self._command_history.remove(history)
                    return
        if execute_command == 'volumeUp':
            await self.async_volume_up(update_request_time=False, execute=False)
        if execute_command == 'volumeDown':
            await self.async_volume_down(update_request_time=False, execute=False)
        if execute_command == 'mute':
            await self.async_mute_volume(not self._attr_is_volume_muted, execute=False)
        if execute_command == 'powerOn' or execute_command == 'powerOff':
            if self._state == STATE_OFF:
                await self.async_turn_on(execute=False)
            else:
                await self.async_turn_off(execute=False)

    async def _homekit_event_handler(self, event):
        data = event.data
        if data['entity_id'] != self.entity_id:
            return
        command = self._homekit_map[data['key_name']]
        date = datetime.now()
        self._last_command_request_time = date
        await self.async_send_ir_command(command, date)


class CommandHistory:

    def __init__(self, command, raw, date):
        self._command = command
        self._raw = raw
        self._date = date

    def command(self):
        return self._command

    def date(self):
        return self._date

    def raw(self):
        return self._raw

    def is_outdate(self):
        return (datetime.now() - self._date).total_seconds() > 60
