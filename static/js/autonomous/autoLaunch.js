/**
 * Factory OS — Autonomous Auto-Launch Module
 * Автоматический запуск атомов и мониторинг выполнения
 */

import { api } from '../api/client.js';
import { buildAutoQueue, getNextAtomToLaunch, updateQueueAfterCompletion } from './autoQueue.js';

// Состояние авто-запуска
const launchState = {
  activeVisionId: null,
  queue: null,
  monitoringInterval: null,
  isRunning: false
};

/**
 * Авто-запуск первого атома Vision
 * @param {string} visionId - ID Vision
 */
export async function autoLaunchVision(visionId) {
  if (launchState.isRunning) {
    console.log('[AutoLaunch] Already running');
    return;
  }
  
  launchState.activeVisionId = visionId;
  launchState.isRunning = true;
  
  try {
    // 1. Построение очереди
    launchState.queue = buildAutoQueue(visionId);
    
    // 2. Запуск первого атома
    await launchNextAtom();
    
    // 3. Запуск мониторинга
    startMonitoring();
    
    return {
      success: true,
      visionId,
      queue: launchState.queue
    };
    
  } catch (error) {
    console.error('[AutoLaunch] Error:', error);
    return {
      success: false,
      error: error.message,
      visionId
    };
  }
}

/**
 * Запуск следующего атома из очереди
 */
async function launchNextAtom() {
  if (!launchState.queue) return;
  
  const atom = getNextAtomToLaunch(launchState.queue);
  if (!atom) {
    console.log('[AutoLaunch] No atoms to launch');
    return;
  }
  
  try {
    // Запуск Forge
    const result = await api.runWorkItem(atom.id);
    
    if (result.ok === false || result.error) {
      throw new Error(result.error || 'Launch failed');
    }
    
    // Обновление UI
    if (window.showFactoryToast) {
      window.showFactoryToast(`Запуск: ${atom.title?.slice(0, 20)}...`, 'ok');
    }
    
  } catch (error) {
    console.error('[AutoLaunch] Launch error:', error);
    if (window.showFactoryToast) {
      window.showFactoryToast(`Ошибка запуска: ${error.message}`, 'err');
    }
  }
}

/**
 * Запуск мониторинга выполнения
 */
function startMonitoring() {
  if (launchState.monitoringInterval) {
    clearInterval(launchState.monitoringInterval);
  }
  
  launchState.monitoringInterval = setInterval(() => {
    monitorQueueProgress();
  }, 5000); // Проверка каждые 5 секунд
}

/**
 * Мониторинг прогресса очереди
 */
async function monitorQueueProgress() {
  try {
    if (!launchState.activeVisionId) return;
    
    // Обновление очереди из store
    const { store } = window;
    if (!store) return;
    
    const newQueue = buildAutoQueue(launchState.activeVisionId);
    
    // Проверка завершённых атомов
    const completedAtoms = launchState.queue?.current.filter(oldAtom => {
      const newAtom = newQueue.current.find(a => a.id === oldAtom.id);
      return !newAtom || newAtom.status === 'done';
    }) || [];
    
    // Обработка завершённых
    for (const atom of completedAtoms) {
      await handleAtomCompletion(atom.id);
    }
    
    // Обновление состояния очереди
    launchState.queue = newQueue;
    
    // Проверка на завершение Vision
    if (isVisionComplete(newQueue)) {
      handleVisionComplete(launchState.activeVisionId);
    }
    
  } catch (error) {
    console.error('[AutoLaunch] Monitor error:', error);
  }
}

/**
 * Обработка завершения атома
 */
async function handleAtomCompletion(atomId) {
  console.log('[AutoLaunch] Atom completed:', atomId);
  
  // Обновление очереди
  launchState.queue = updateQueueAfterCompletion(launchState.queue, atomId);
  
  // Запуск следующего атома
  await launchNextAtom();
}

/**
 * Проверка завершён ли Vision
 */
function isVisionComplete(queue) {
  return queue.total > 0 && 
         queue.completed === queue.total &&
         queue.current.length === 0 &&
         (!queue.queued || queue.queued.length === 0);
}

/**
 * Обработка завершения Vision
 */
function handleVisionComplete(visionId) {
  console.log('[AutoLaunch] Vision complete:', visionId);
  
  stopMonitoring();
  launchState.isRunning = false;
  
  // Уведомление пользователя
  if (window.showFactoryToast) {
    window.showFactoryToast('Vision завершён! 🎉', 'ok');
  }
  
  // TODO: Переход на ResultView (Phase 6)
}

/**
 * Остановка мониторинга
 */
function stopMonitoring() {
  if (launchState.monitoringInterval) {
    clearInterval(launchState.monitoringInterval);
    launchState.monitoringInterval = null;
  }
}

/**
 * Остановка авто-запуска
 */
export function stopAutoLaunch() {
  if (launchState.monitoringInterval) {
    clearInterval(launchState.monitoringInterval);
    launchState.monitoringInterval = null;
  }
  launchState.isRunning = false;
  launchState.activeVisionId = null;
  launchState.queue = null;
}

/**
 * Получение состояния авто-запуска
 */
export function getLaunchState() {
  return {
    ...launchState,
    isPaused: window.factoryPaused === true
  };
}

/**
 * Глобальные функции для использования в UI
 */
window.autoLaunchVision = autoLaunchVision;
window.stopAutoLaunch = stopAutoLaunch;
window.getLaunchState = getLaunchState;
