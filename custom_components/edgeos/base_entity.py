import sys
import logging

from typing import Optional

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity_registry import async_get_registry, EntityRegistry

from .const import *

_LOGGER = logging.getLogger(__name__)


async def _async_setup_entry(hass, entry, async_add_entities, domain, component):
    """Set up EdgeOS based off an entry."""
    _LOGGER.debug(f"Starting async_setup_entry {domain}")

    try:
        entry_data = entry.data
        name = entry_data.get(CONF_NAME)

        ha = _get_ha(hass, name)
        entity_manager = ha.entity_manager
        entity_manager.set_domain_component(domain, async_add_entities, component)
    except Exception as ex:
        exc_type, exc_obj, tb = sys.exc_info()
        line_number = tb.tb_lineno

        _LOGGER.error(f"Failed to load {domain}, error: {ex}, line: {line_number}")


class EdgeOSEntity(Entity):
    """Representation a binary sensor that is updated by EdgeOS."""

    def __init__(self, hass, ha, entity, current_domain):
        """Initialize the EdgeOS Binary Sensor."""
        self._hass = hass
        self._entity = entity
        self._remove_dispatcher = None
        self._ha = ha
        self._entity_manager = ha.entity_manager
        self._device_manager = ha.device_manager
        self._current_domain = current_domain

    @property
    def unique_id(self) -> Optional[str]:
        """Return the name of the node."""
        return f"{DEFAULT_NAME}-{self._current_domain}-{self.name}"

    @property
    def device_info(self):
        device_name = self._entity.get(ENTITY_DEVICE_NAME)

        return self._device_manager.get(device_name)

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def name(self):
        """Return the name of the binary sensor."""
        return self._entity.get(ENTITY_NAME)

    @property
    def icon(self) -> Optional[str]:
        """Return the icon of the sensor."""
        return self._entity.get(ENTITY_ICON)

    @property
    def device_state_attributes(self):
        """Return true if the binary sensor is on."""
        return self._entity.get(ENTITY_ATTRIBUTES, {})

    async def async_added_to_hass(self):
        """Register callbacks."""
        _LOGGER.info(f"async_added_to_hass: {self.unique_id}")

        async_dispatcher_connect(self._hass,
                                 SIGNALS[self._current_domain],
                                 self._schedule_immediate_update)

        self._entity_manager.set_entity_status(self._current_domain, self.name, ENTITY_STATUS_READY)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_dispatcher is not None:
            self._remove_dispatcher()

    async def async_update_data(self):
        if self._entity_manager is None:
            _LOGGER.debug(f"Cannot update {self._current_domain} - Entity Manager is None | {self.name}")
        else:
            self._entity = self._entity_manager.get_entity(self._current_domain, self.name)

            if self._entity is None:
                _LOGGER.debug(f"Cannot update {self._current_domain} - Entity was not found | {self.name}")
            elif self._entity.get(ENTITY_STATUS, ENTITY_STATUS_EMPTY) == ENTITY_STATUS_CANCELLED:
                _LOGGER.debug(f"Update {self._current_domain} - Entity was removed | {self.name}")

                self._entity_manager.delete_entity(self._current_domain, self.name)
            else:
                _LOGGER.debug(f"Update {self._current_domain} -> {self.name}")

                self._entity_manager.set_entity_status(self._current_domain, self.name, ENTITY_STATUS_READY)

                self.async_schedule_update_ha_state(True)

    @callback
    def _schedule_immediate_update(self):
        self.hass.async_add_job(self.async_update_data)


def _get_ha(hass, name):
    ha_data = hass.data.get(DATA_EDGEOS, {})
    ha = ha_data.get(name)

    return ha