import logging
import asyncio
from homeassistant.components.vacuum import StateVacuumEntity, VacuumEntityFeature, VacuumActivity

_LOGGER = logging.getLogger(__name__)

# Глобальные переменные для группировки
_PENDING_ROOMS = set()
_TIMER_HANDLE = None

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Настройка платформы (Динамический поиск комнат)."""
    # В вашей конфигурации мастер-пылесос лежит в CONF_MASTER_VACUUM или main_vacuum
    # Пытаемся достать оба варианта
    master_id = config_entry.data.get("main_vacuum") or config_entry.data.get("master_vacuum")
    
    if not master_id:
        _LOGGER.error("Не найден ID основного пылесоса в настройках")
        return False

    # Ищем объект карты и комнаты (ваш рабочий метод)
    map_entities = hass.states.async_entity_ids("image")
    rooms_found = {}

    for entity_id in map_entities:
        state = hass.states.get(entity_id)
        if state and "rooms" in state.attributes:
            rooms_found = state.attributes["rooms"]
            _LOGGER.info(f"Найдена карта {entity_id} с комнатами: {rooms_found}")
            break

    entities = []
    for r_id, r_info in rooms_found.items():
        # Определяем имя комнаты (ваш фикс)
        if hasattr(r_info, "name"):
            room_name = r_info.name
        elif isinstance(r_info, dict):
            room_name = r_info.get("name", f"Комната {r_id}")
        else:
            room_name = f"Комната {r_id}"
            
        entities.append(RoborockZoneEntity(hass, room_name, r_id, master_id))

    if entities:
        _LOGGER.info(f"Добавляем {len(entities)} виртуальных пылесосов")
        async_add_entities(entities)
    else:
        _LOGGER.warning("Комнаты не найдены в атрибутах карты!")
        
    return True

class RoborockZoneEntity(StateVacuumEntity):
    def __init__(self, hass, name, room_id, master):
        self.hass = hass
        self._room_id = int(room_id)
        self._master = master
        self._attr_name = f"Уборка {name}"
        self._attr_unique_id = f"roborock_vr_{master.split('.')[-1]}_{room_id}"
        self._attr_supported_features = (
            VacuumEntityFeature.START | 
            VacuumEntityFeature.STOP | 
            VacuumEntityFeature.RETURN_HOME
        )

    @property
    def activity(self):
        """Состояние для HA 2026."""
        master_state = self.hass.states.get(self._master)
        if not master_state:
            return VacuumActivity.IDLE
        
        s = master_state.state
        if s == "cleaning": return VacuumActivity.CLEANING
        if s == "returning": return VacuumActivity.RETURNING
        if s == "docked": return VacuumActivity.DOCKED
        if s == "paused": return VacuumActivity.PAUSED
        return VacuumActivity.IDLE

    async def async_start(self):
        """Умный запуск с ожиданием 2 секунды."""
        global _TIMER_HANDLE, _PENDING_ROOMS
        
        if self.activity == VacuumActivity.CLEANING:
            _LOGGER.warning("Пылесос уже занят, игнорируем")
            return

        _PENDING_ROOMS.add(self._room_id)

        if _TIMER_HANDLE:
            _TIMER_HANDLE.cancel()
            
        # Ждем 2 секунды, чтобы собрать все нажатые комнаты
        _TIMER_HANDLE = self.hass.loop.call_later(
            2, lambda: self.hass.async_create_task(self._execute_batch())
        )

    async def _execute_batch(self):
        global _PENDING_ROOMS
        if not _PENDING_ROOMS: return

        rooms_list = list(_PENDING_ROOMS)
        _PENDING_ROOMS.clear()
        
        _LOGGER.info(f"🚀 Запуск пакетной уборки комнат: {rooms_list}")
        
        # Используем универсальный вызов сервиса
        await self.hass.services.async_call("vacuum", "send_command", {
            "entity_id": self._master,
            "command": "app_segment_clean",
            "params": rooms_list
        })

    async def async_stop(self, **kwargs):
        await self.hass.services.async_call("vacuum", "stop", {"entity_id": self._master})

    async def async_return_to_base(self, **kwargs):
        await self.hass.services.async_call("vacuum", "return_to_base", {"entity_id": self._master})
