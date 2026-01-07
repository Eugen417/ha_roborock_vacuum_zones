# v1.0.2
import logging
import asyncio
from homeassistant.components.vacuum import StateVacuumEntity, VacuumEntityFeature, VacuumActivity

_LOGGER = logging.getLogger(__name__)

# Глобальные переменные для реализации очереди (Batching)
_PENDING_ROOMS = set()
_TIMER_HANDLE = None

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Настройка платформы: динамический поиск комнат при старте."""
    # Получаем ID основного пылесоса из настроек
    master_id = config_entry.data.get("main_vacuum") or config_entry.data.get("master_vacuum")
    
    if not master_id:
        _LOGGER.error("Не найден ID основного пылесоса в конфигурации!")
        return False

    # Ищем объект карты (image.*), чтобы достать из него комнаты
    map_entities = hass.states.async_entity_ids("image")
    rooms_found = {}

    for entity_id in map_entities:
        state = hass.states.get(entity_id)
        if state and "rooms" in state.attributes:
            rooms_found = state.attributes["rooms"]
            _LOGGER.info(f"Найдена карта {entity_id} с комнатами: {rooms_found}")
            break

    if not rooms_found:
        _LOGGER.warning("Атрибут 'rooms' не найден ни в одной сущности image. Виртуальные пылесосы не созданы.")
        return True

    entities = []
    for r_id, r_info in rooms_found.items():
        # Определяем имя комнаты (поддержка разных форматов данных из карты)
        if hasattr(r_info, "name"):
            room_name = r_info.name
        elif isinstance(r_info, dict):
            room_name = r_info.get("name", f"Room {r_id}")
        else:
            room_name = f"Room {r_id}"
            
        entities.append(RoborockZoneEntity(hass, room_name, r_id, master_id))

    if entities:
        _LOGGER.info(f"Добавляем {len(entities)} виртуальных зон для пылесоса {master_id}")
        async_add_entities(entities, update_before_add=True)
        
    return True

class RoborockZoneEntity(StateVacuumEntity):
    """Сущность виртуального пылесоса для конкретной комнаты."""

    def __init__(self, hass, name, room_id, master):
        self.hass = hass
        self._room_id = int(room_id)
        self._master = master
        self._attr_name = f"Уборка {name}"
        # Генерируем уникальный ID на основе ID мастера и комнаты
        master_slug = master.split('.')[-1]
        self._attr_unique_id = f"roborock_vr_{master_slug}_{room_id}"
        
        self._attr_supported_features = (
            VacuumEntityFeature.START | 
            VacuumEntityFeature.STOP | 
            VacuumEntityFeature.RETURN_HOME
        )

    @property
    def activity(self):
        """Определяем состояние, транслируя статус основного пылесоса (HA 2026+)."""
        master_state = self.hass.states.get(self._master)
        if not master_state:
            return VacuumActivity.IDLE
        
        s = master_state.state
        if s == "cleaning": return VacuumActivity.CLEANING
        if s == "returning": return VacuumActivity.RETURNING
        if s == "docked": return VacuumActivity.DOCKED
        if s == "paused": return VacuumActivity.PAUSED
        if s == "error": return VacuumActivity.ERROR
        return VacuumActivity.IDLE

    async def async_start(self):
        """Запуск уборки с использованием интеллектуальной очереди."""
        global _TIMER_HANDLE, _PENDING_ROOMS
        
        # Защита: если пылесос уже убирает, не шлем новые команды (чтобы не сбить карту)
        if self.activity == VacuumActivity.CLEANING:
            _LOGGER.warning(f"Пылесос {self._master} уже выполняет уборку. Команда игнорирована.")
            return

        # Добавляем ID текущей комнаты в общую очередь
        _PENDING_ROOMS.add(self._room_id)

        # Сбрасываем старый таймер, если он был активен
        if _TIMER_HANDLE:
            _TIMER_HANDLE.cancel()
            
        # Запускаем таймер на 2 секунды накопления
        _TIMER_HANDLE = self.hass.loop.call_later(
            2, lambda: self.hass.async_create_task(self._execute_batch_clean())
        )
        _LOGGER.debug(f"Комната {self._room_id} добавлена в пакет. Ожидание 2 сек...")

    async def _execute_batch_clean(self):
        """Отправка накопленного списка комнат одной командой."""
        global _PENDING_ROOMS
        if not _PENDING_ROOMS:
            return

        # Копируем список и очищаем очередь
        rooms_to_clean = list(_PENDING_ROOMS)
        _PENDING_ROOMS.clear()
        
        _LOGGER.info(f"🚀 Инициация уборки сегментов: {rooms_to_clean}")
        
        try:
            # Вызов сервиса Roborock для мульти-уборки зон
            await self.hass.services.async_call(
                "vacuum", "send_command",
                {
                    "entity_id": self._master,
                    "command": "app_segment_clean",
                    "params": rooms_to_clean
                },
                blocking=True
            )
        except Exception as e:
            _LOGGER.error(f"Ошибка при пакетном запуске Roborock: {e}")

    async def async_stop(self, **kwargs):
        """Принудительная остановка всего пылесоса."""
        _PENDING_ROOMS.clear()
        await self.hass.services.async_call("vacuum", "stop", {"entity_id": self._master})

    async def async_return_to_base(self, **kwargs):
        """Возврат на базу."""
        _PENDING_ROOMS.clear()
        await self.hass.services.async_call("vacuum", "return_to_base", {"entity_id": self._master})
