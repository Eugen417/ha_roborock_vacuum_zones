from homeassistant.components.vacuum import StateVacuumEntity, VacuumEntityFeature
from .const import DOMAIN, CONF_MASTER_VACUUM

async def async_setup_entry(hass, config_entry, async_add_entities):
    master_id = config_entry.data[CONF_MASTER_VACUUM]
    entities = []

    # Ищем объект карты
    map_entities = hass.states.async_entity_ids("image")
    rooms_found = {}

    for entity_id in map_entities:
        state = hass.states.get(entity_id)
        if state and "rooms" in state.attributes:
            rooms_found = state.attributes["rooms"]
            break

    # Обработка комнат с проверкой на объект (фикс для вашего лога)
    for r_id, r_info in rooms_found.items():
        if hasattr(r_info, "name"):
            room_name = r_info.name
        elif isinstance(r_info, dict):
            room_name = r_info.get("name", f"Комната {r_id}")
        else:
            room_name = f"Комната {r_id}"
            
        entities.append(RoborockZoneEntity(room_name, r_id, master_id, hass))

    if entities:
        async_add_entities(entities)

class RoborockZoneEntity(StateVacuumEntity):
    def __init__(self, name, room_id, master, hass):
        self._hass = hass
        self._room_id = int(room_id)
        self._master = master
        self._attr_name = f"Уборка {name}"
        self._attr_unique_id = f"roborock_vr_{master}_{room_id}"
        self._attr_supported_features = (
            VacuumEntityFeature.START | 
            VacuumEntityFeature.STOP | 
            VacuumEntityFeature.RETURN_HOME
        )

    @property
    def state(self):
        master_state = self._hass.states.get(self._master)
        return master_state.state if master_state else "unknown"

    async def async_start(self):
        await self._hass.services.async_call("vacuum", "send_command", {
            "entity_id": self._master,
            "command": "app_segment_clean",
            "params": [self._room_id]
        })

    async def async_stop(self, **kwargs):
        await self._hass.services.async_call("vacuum", "stop", {"entity_id": self._master})

    async def async_return_to_base(self, **kwargs):
        await self._hass.services.async_call("vacuum", "return_to_base", {"entity_id": self._master})
