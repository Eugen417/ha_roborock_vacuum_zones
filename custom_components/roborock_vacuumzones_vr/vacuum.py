import logging
import asyncio
from homeassistant.components.vacuum import StateVacuumEntity, VacuumEntityFeature
from homeassistant.const import STATE_CLEANING, STATE_RETURNING, STATE_IDLE, STATE_DOCKED

_LOGGER = logging.getLogger(__name__)

# Глобальные переменные для координации запуска
_PENDING_ROOMS = set()
_TIMER_HANDLE = None

async def async_setup_entry(hass, config_entry, async_add_entities):
    main_vacuum = config_entry.data.get("main_vacuum")
    rooms = config_entry.data.get("rooms", {})
    
    entities = [RoborockRoomVacuum(main_vacuum, rid, rname) for rid, rname in rooms.items()]
    async_add_entities(entities)

class RoborockRoomVacuum(StateVacuumEntity):
    def __init__(self, main_vacuum, room_id, room_name):
        self._main_vacuum = main_vacuum
        self._room_id = int(room_id)
        self._attr_name = f"Clean {room_name}"
        self._attr_unique_id = f"v_vac_{room_id}_{main_vacuum}"
        self._attr_supported_features = (
            VacuumEntityFeature.START | 
            VacuumEntityFeature.STOP | 
            VacuumEntityFeature.RETURN_HOME
        )

    @property
    def state(self):
        """Отображаем состояние основного пылесоса."""
        main_state = self.hass.states.get(self._main_vacuum)
        return main_state.state if main_state else None

    async def async_start(self):
        """Интеллектуальный запуск зоны."""
        global _TIMER_HANDLE, _PENDING_ROOMS
        
        main_state = self.hass.states.get(self._main_vacuum)
        
        # ЕСЛИ ПЫЛЕСОС УЖЕ УБИРАЕТ:
        # Мы не перебиваем его, чтобы не сбить текущий прогресс.
        if main_state and main_state.state == STATE_CLEANING:
            _LOGGER.warning(f"Пылесос {self._main_vacuum} уже занят уборкой. Команда для комнаты {self._room_id} проигнорирована.")
            return

        # Добавляем комнату в пакет на отправку
        _PENDING_ROOMS.add(self._room_id)
        
        # Сбрасываем старый таймер, если он был, и запускаем новый (окно 2 сек)
        if _TIMER_HANDLE:
            _TIMER_HANDLE.cancel()
            
        _TIMER_HANDLE = self.hass.loop.call_later(
            2, lambda: self.hass.async_create_task(self._execute_batch_clean())
        )
        _LOGGER.info(f"Комната {self._room_id} добавлена в пакет запуска. Ждем завершения выбора...")

    async def _execute_batch_clean(self):
        """Сборная отправка всех выбранных сегментов."""
        global _PENDING_ROOMS
        
        if not _PENDING_ROOMS:
            return

        rooms_list = list(_PENDING_ROOMS)
        _PENDING_ROOMS.clear() # Очищаем буфер перед отправкой
        
        _LOGGER.info(f"Инициация уборки сегментов: {rooms_list}")
        
        try:
            await self.hass.services.async_call(
                "roborock", "vacuum_clean_segment",
                {
                    "entity_id": self._main_vacuum,
                    "segments": rooms_list,
                    "repeats": 1
                },
                blocking=True # Ждем подтверждения от сервиса
            )
        except Exception as e:
            _LOGGER.error(f"Ошибка при отправке команды Roborock: {e}")

    async def async_stop(self):
        """Полная остановка и сброс пакетов."""
        _PENDING_ROOMS.clear()
        await self.hass.services.async_call("vacuum", "stop", {"entity_id": self._main_vacuum})

    async def async_return_to_base(self):
        """Возврат домой."""
        _PENDING_ROOMS.clear()
        await self.hass.services.async_call("vacuum", "return_to_base", {"entity_id": self._main_vacuum})
