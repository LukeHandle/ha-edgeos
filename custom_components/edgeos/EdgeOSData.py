"""
This component provides support for Home Automation Manager (HAM).
For more details about this component, please refer to the documentation at
https://home-assistant.io/components/edgeos/
"""
import sys
import logging

from .const import *
from .web_api import EdgeOSWebAPI
from .web_login import EdgeOSWebLogin
from .web_socket import EdgeOSWebSocket

_LOGGER = logging.getLogger(__name__)


class EdgeOSData(object):
    def __init__(self, hass, entry_data, update_home_assistant):
        self._hass = hass
        self._update_home_assistant = update_home_assistant

        self._is_initialized = False

        self._host = entry_data.get(CONF_HOST)
        self._username = entry_data.get(CONF_USERNAME, DEFAULT_USERNAME)
        self._password = entry_data.get(CONF_PASSWORD)
        self._unit = entry_data.get(CONF_UNIT, ATTR_BYTE)
        self._edgeos_url = API_URL_TEMPLATE.format(self._host)

        self._is_updating = False
        self._edgeos_data = {}
        self._system_data = {}

        self._ws_handlers = self.get_ws_handlers()
        self._topics = self._ws_handlers.keys()

        self._api = EdgeOSWebAPI(self._hass, self._edgeos_url, self.edgeos_disconnection_handler)

        self._ws = EdgeOSWebSocket(self._hass,
                                   self._edgeos_url,
                                   self._topics,
                                   self.ws_handler)

        self._edgeos_login_service = EdgeOSWebLogin(self._host, self._username, self._password)

    @property
    def edgeos_data(self):
        return self._edgeos_data

    @property
    def system_data(self):
        return self._system_data

    async def initialize(self, call_after_refresh=None):
        try:
            if self._edgeos_login_service.login():
                cookies = self._edgeos_login_service.cookies_data
                session_id = self._edgeos_login_service.session_id

                _LOGGER.debug(f'Initializing API')

                await self._api.initialize(cookies)

                _LOGGER.debug(f'Requesting initial data')
                await self.refresh()

                if call_after_refresh is not None:
                    await call_after_refresh()

                _LOGGER.debug(f'Initializing WS using session: {session_id}')
                await self._ws.initialize(cookies, session_id)
        except Exception as ex:
            exc_type, exc_obj, tb = sys.exc_info()
            line_number = tb.tb_lineno

            _LOGGER.error(f"Failed to initialize EdgeOS Manager, Error: {str(ex)}, Line: {line_number}")

    @property
    def is_initialized(self):
        return self._is_initialized

    def log_events(self, log_event_enabled):
        self._ws.log_events(log_event_enabled)

    async def edgeos_disconnection_handler(self):
        _LOGGER.debug(f'Disconnection detected, reconnecting...')

        await self.terminate()

        await self.initialize()

    async def terminate(self):
        try:
            _LOGGER.debug(f'Terminating WS')

            await self._ws.close()

            _LOGGER.debug(f'WS terminated')
        except Exception as ex:
            exc_type, exc_obj, tb = sys.exc_info()
            line_number = tb.tb_lineno

            _LOGGER.error(f"Failed to terminate connection to WS, Error: {ex}, Line: {line_number}")

    async def refresh(self):
        await self._api.heartbeat()
        await self.load_devices_data()
        await self.load_unknown_devices()

        self.update()

    def update(self, force=False):
        try:
            if not force and self._is_updating:
                return

            self._is_updating = True

            devices = self.get_devices()
            interfaces = self.get_interfaces()
            system_state = self.get_system_state()
            unknown_devices = self.get_unknown_devices()

            api_last_update = self._api.last_update
            web_socket_last_update = self._ws.last_update

            if system_state is not None:
                system_state[IS_ALIVE] = self._api.is_connected

            self._system_data = {
                INTERFACES_KEY: interfaces,
                STATIC_DEVICES_KEY: devices,
                UNKNOWN_DEVICES_KEY: unknown_devices,
                SYSTEM_STATS_KEY: system_state,
                ATTR_API_LAST_UPDATE: api_last_update,
                ATTR_WEB_SOCKET_LAST_UPDATE: web_socket_last_update
            }

            self._update_home_assistant()
        except Exception as ex:
            exc_type, exc_obj, tb = sys.exc_info()
            line_number = tb.tb_lineno

            _LOGGER.error(f'Failed to refresh data, Error: {ex}, Line: {line_number}')

        self._is_updating = False

    def ws_handler(self, payload=None):
        try:
            if payload is not None:
                for key in payload:
                    _LOGGER.debug(f"Running parser of {key}")

                    data = payload.get(key)
                    handler = self._ws_handlers.get(key)

                    if handler is None:
                        _LOGGER.error(f'Handler not found for {key}')
                    else:
                        handler(data)
        except Exception as ex:
            exc_type, exc_obj, tb = sys.exc_info()
            line_number = tb.tb_lineno

            _LOGGER.error(f'Failed to handle WS message, Error: {ex}, Line: {line_number}')

    def get_ws_handlers(self):
        ws_handlers = {
            EXPORT_KEY: self.handle_export,
            INTERFACES_KEY: self.handle_interfaces,
            SYSTEM_STATS_KEY: self.handle_system_stats,
            DISCOVER_KEY: self.handle_discover
        }

        return ws_handlers

    async def load_unknown_devices(self):
        try:
            _LOGGER.debug('Getting unknown devices by API')

            unknown_devices_data = await self._api.get_general_data(DHCP_LEASES_KEY)

            if unknown_devices_data is not None:
                result = []

                dhcp_server_leases_data = unknown_devices_data.get('dhcp-server-leases', {})

                for interface_key in dhcp_server_leases_data:
                    interface_info = dhcp_server_leases_data[interface_key]

                    for ip in interface_info:
                        device_info = interface_info[ip]

                        device = {
                            "ip": ip,
                            "expiration": device_info.get("expiration"),
                            "pool": device_info.get("pool"),
                            "mac": device_info.get("mac"),
                            "client-hostname": device_info.get("client-hostname")
                        }

                        result.append(device)

                self.set_unknown_devices(result)
            else:
                _LOGGER.warning(f"Invalid data: {unknown_devices_data}")
        except Exception as ex:
            exc_type, exc_obj, tb = sys.exc_info()
            line_number = tb.tb_lineno

            _LOGGER.error(f'Failed to load devices data, Error: {ex}, Line: {line_number}')

    def load_devices(self, device_data):
        if device_data is None:
            device_data = {}

        service_data = device_data.get(SERVICE, {})
        dhcp_server_data = service_data.get(DHCP_SERVER, {})
        shared_network_data = dhcp_server_data.get(SHARED_NETWORK_NAME, {})

        for shared_network_key in shared_network_data:
            shared_network_item = shared_network_data[shared_network_key]
            subnet_data = shared_network_item.get(SUBNET, {})
            for subnet_key in subnet_data:
                subnet_item = subnet_data[subnet_key]

                static_mapping_data = subnet_item.get(STATIC_MAPPING, {})
                for hostname in static_mapping_data:
                    device = self.get_device(hostname)

                    static_mapping_item = static_mapping_data[hostname]
                    ip = static_mapping_item.get(IP_ADDRESS)
                    mac = static_mapping_item.get(MAC_ADDRESS)

                    name = hostname
                    if ip is not None:
                        name = f"{hostname} ({ip})"

                    device[IP] = ip
                    device[MAC] = mac
                    device[ATTR_NAME] = name

                    self.set_device(hostname, device)

    def load_interfaces(self, device_data):
        interfaces_data = device_data.get(INTERFACES_KEY, {})
        ethernet_data = interfaces_data.get("ethernet", {})

        for ethernet_key in ethernet_data:
            ethernet_item = ethernet_data[ethernet_key]
            description = ethernet_item.get("description")

            name = ethernet_key

            if description is not None:
                name = f"{ethernet_key} ({description})"

            interface = {
                ATTR_NAME: name
            }

            self.set_interface(ethernet_key, interface)

    async def load_devices_data(self):
        try:
            _LOGGER.debug('Getting devices by API')

            devices_data = await self._api.get_devices_data()

            self.load_devices(devices_data)
            self.load_interfaces(devices_data)

            self.update()

        except Exception as ex:
            exc_type, exc_obj, tb = sys.exc_info()
            line_number = tb.tb_lineno

            _LOGGER.error(f'Failed to load devices data, Error: {ex}, Line: {line_number}')

    def handle_interfaces(self, data):
        try:
            _LOGGER.debug(f'Handle {INTERFACES_KEY} data')

            if data is None or data == '':
                _LOGGER.debug(f'{INTERFACES_KEY} is empty')
                return

            for name in data:
                interface_data = data.get(name)

                interface = {}

                for item in interface_data:
                    item_data = interface_data.get(item)

                    if ADDRESS_LIST == item:
                        interface[item] = item_data

                    elif INTERFACES_STATS == item:
                        for stats_item in INTERFACES_STATS_MAP:
                            interface[stats_item] = item_data.get(stats_item)

                    else:
                        if item in INTERFACES_MAIN_MAP:
                            interface[item] = item_data

                self.set_interface(name, interface)

            self.update()
        except Exception as ex:
            exc_type, exc_obj, tb = sys.exc_info()
            line_number = tb.tb_lineno

            _LOGGER.error(f'Failed to load {INTERFACES_KEY}, Error: {ex}, Line: {line_number}')

    def handle_system_stats(self, data):
        try:
            _LOGGER.debug(f'Handle {SYSTEM_STATS_KEY} data')

            if data is None or data == '':
                _LOGGER.debug(f'{SYSTEM_STATS_KEY} is empty')
                return

            self.set_system_state(data)
        except Exception as ex:
            exc_type, exc_obj, tb = sys.exc_info()
            line_number = tb.tb_lineno

            _LOGGER.error(f'Failed to load {SYSTEM_STATS_KEY}, Error: {ex}, Line: {line_number}')

    def handle_discover(self, data):
        try:
            _LOGGER.debug(f'Handle {DISCOVER_KEY} data')

            result = self.get_discover_data()

            if data is None or data == '':
                _LOGGER.debug(f'{DISCOVER_KEY} is empty')
                return

            devices_data = data.get(DEVICE_LIST, [])

            for device_data in devices_data:
                for key in DISCOVER_DEVICE_ITEMS:
                    device_data_item = device_data.get(key, {})

                    if key == ADDRESS_LIST:
                        discover_addresses = {}

                        for address in device_data_item:
                            hwaddr = address.get(ADDRESS_HWADDR)
                            ipv4 = address.get(ADDRESS_IPV4)

                            discover_addresses[hwaddr] = ipv4

                        result[key] = discover_addresses
                    else:
                        result[key] = device_data_item

            self.set_discover_data(result)
        except Exception as ex:
            exc_type, exc_obj, tb = sys.exc_info()
            line_number = tb.tb_lineno

            _LOGGER.error(f'Failed to load {DISCOVER_KEY}, Original Message: {data}, Error: {ex}, Line: {line_number}')

    @staticmethod
    def check_last_activity(device):
        date_minimum = datetime.fromtimestamp(0)
        device_ip = device.get(IP)
        device_connected = device.get(CONNECTED, False)
        device_last_activity = device.get(LAST_ACTIVITY, date_minimum)

        is_connected = FALSE_STR

        time_since_last_action = (datetime.now() - device_last_activity).total_seconds()

        if time_since_last_action < DISCONNECTED_INTERVAL:
            is_connected = TRUE_STR
        else:
            if device_connected != is_connected and device_last_activity != date_minimum:
                msg = [
                    f"Device {device_ip} disconnected",
                    f"due to inactivity since {device_last_activity}",
                    f"({time_since_last_action} seconds"
                ]

                _LOGGER.info(" ".join(msg))

        device[CONNECTED] = is_connected

    def handle_export(self, data):
        try:
            _LOGGER.debug(f'Handle {EXPORT_KEY} data')

            if data is None or data == '':
                _LOGGER.debug(f'{EXPORT_KEY} is empty')
                return

            all_devices = self.get_devices().keys()
            updated_devices = []

            for device_key in all_devices:
                device = self.get_device(device_key)
                device_ip = device.get(IP)
                device_data = data.get(device_ip)

                if device_data is None:
                    self.check_last_activity(device)
                    updated_devices.append({
                        CONF_HOST: device_key,
                        "device": device
                    })

                    continue

                traffic = {}
                for item in DEVICE_SERVICES_STATS_MAP:
                    traffic[item] = int(0)

                for service in device_data:
                    service_data = device_data.get(service, {})
                    for item in service_data:
                        current_value = traffic.get(item, 0)
                        service_data_item_value = 0

                        if item in service_data and service_data[item] != '':
                            service_data_item_value = int(service_data[item])

                        if 'x_rate' in item and current_value > 0:
                            device[LAST_ACTIVITY] = datetime.now()

                        traffic_value = current_value + service_data_item_value

                        traffic[item] = traffic_value
                        device[item] = traffic_value

                self.check_last_activity(device)
                updated_devices.append({
                    CONF_HOST: device_key,
                    "device": device
                })

            for updated_device in updated_devices:
                hostname = updated_device.get(CONF_HOST)
                device = updated_device.get("device")

                self.set_device(hostname, device)

        except Exception as ex:
            exc_type, exc_obj, tb = sys.exc_info()
            line_number = tb.tb_lineno

            _LOGGER.error(f'Failed to load {EXPORT_KEY}, Error: {ex}, Line: {line_number}')

    def set_discover_data(self, discover_state):
        self._edgeos_data[DISCOVER_KEY] = discover_state

        self.update()

    def get_discover_data(self):
        result = self._edgeos_data.get(DISCOVER_KEY, {})

        return result

    def set_unknown_devices(self, unknown_devices):
        self._edgeos_data[UNKNOWN_DEVICES_KEY] = unknown_devices

        self.update()

    def get_unknown_devices(self):
        result = self._edgeos_data.get(UNKNOWN_DEVICES_KEY, {})

        return result

    def set_system_state(self, system_state):
        self._edgeos_data[SYSTEM_STATS_KEY] = system_state

        self.update()

    def get_system_state(self):
        result = self._edgeos_data.get(SYSTEM_STATS_KEY, {})

        return result

    def set_interface(self, name, interface):
        all_interfaces = self.get_interfaces()

        if name not in all_interfaces:
            all_interfaces[name] = {}

        current_interface = all_interfaces[name]

        for key in interface:
            current_interface[key] = interface[key]

    def get_interfaces(self):
        if INTERFACES_KEY not in self._edgeos_data:
            self._edgeos_data[INTERFACES_KEY] = {}

        result = self._edgeos_data.get(INTERFACES_KEY, {})

        return result

    def get_interface(self, name):
        interfaces = self.get_interfaces()
        interface = interfaces.get(name, {})

        return interface

    def get_devices(self):
        if STATIC_DEVICES_KEY not in self._edgeos_data:
            self._edgeos_data[STATIC_DEVICES_KEY] = {}

        result = self._edgeos_data[STATIC_DEVICES_KEY]

        return result

    def set_device(self, hostname, device):
        all_devices = self.get_devices()

        if hostname not in all_devices:
            all_devices[hostname] = {}

        current_device = all_devices[hostname]

        for key in device:
            current_device[key] = device[key]

    def get_device(self, hostname):
        devices = self.get_devices()
        device = devices.get(hostname, {})

        return device

    @staticmethod
    def get_device_name(hostname):
        name = f'{DEFAULT_NAME} {hostname}'

        return name

    def get_device_mac(self, hostname):
        device = self.get_device(hostname)

        mac = device.get(MAC)

        return mac

    def is_device_online(self, hostname):
        device = self.get_device(hostname)

        connected = device.get(CONNECTED, FALSE_STR)

        if connected == TRUE_STR:
            is_online = True
        else:
            is_online = False

        return is_online
