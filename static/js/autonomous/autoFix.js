/**
 * Factory OS — Autonomous Auto-Fix Module
 * Автоматическое исправление ошибок Forge
 */

import { api } from '../api/client.js';

// Состояние авто-исправления
const fixState = {
  handlingRuns: new Set(),
  maxAttempts: 3
};

/**
 * Мониторинг ошибок для Vision
 * @param {string} visionId - ID Vision
 */
export async function monitorForErrors(visionId) {
  const journal = window.store?.state?.journal?.items || [];
  
  const failedRuns = journal.filter(item => 
    (item.event_type?.includes('forge_failed') ||
     item.status === 'failed' ||
     item.severity === 'error') &&
    item.vision_id === visionId
  );
  
  for (const run of failedRuns) {
    if (!run.handled && !fixState.handlingRuns.has(run.id)) {
      await handleFailedRun(run);
    }
  }
}

/**
 * Обработка неудачного прогона
 * @param {Object} run - Объект run из журнала
 */
async function handleFailedRun(run) {
  // Помечаем как обрабатываемый
  fixState.handlingRuns.add(run.id);
  
  try {
    // 1. Анализ ошибки
    const error = await analyzeRunError(run.id);
    
    // 2. Запускаем авто-исправление
    const result = await autoFixFailedRun(run.id, error);
    
    if (!result.success) {
      // 3. Эскалация пользователю
      escalateToUser(run, result);
    }
    
  } catch (error) {
    console.error('[AutoFix] Handle error:', error);
    escalateToUser(run, { success: false, error: error.message });
  } finally {
    fixState.handlingRuns.delete(run.id);
  }
}

/**
 * Анализ ошибки прогона
 * @param {string} runId - ID прогона
 * @returns {Promise<Object>} Информация об ошибке
 */
async function analyzeRunError(runId) {
  try {
    // Получаем информацию о прогоне
    const runInfo = await api.getRun(runId);
    
    // Получаем шаги прогона
    const steps = await api.getRunSteps(runId);
    
    // Находим последнюю ошибку
    const lastError = steps?.find(s => s.status === 'failed' || s.error);
    
    return {
      runId,
      type: lastError?.step_kind || 'unknown',
      message: lastError?.error || lastError?.message || 'Unknown error',
      context: {
        workItemId: runInfo?.work_item_id,
        files: runInfo?.files || [],
        steps: steps || []
      },
      attempts: 0,
      attemptsHistory: []
    };
    
  } catch (error) {
    console.error('[AutoFix] Analyze error:', error);
    return {
      runId,
      type: 'analysis_failed',
      message: error.message,
      context: {},
      attempts: 0,
      attemptsHistory: []
    };
  }
}

/**
 * Авто-исправление неудачного прогона
 * @param {string} runId - ID прогона
 * @param {Object} error - Информация об ошибке
 * @param {number} attempt - Номер попытки
 * @returns {Promise<Object>} Результат
 */
export async function autoFixFailedRun(runId, error, attempt = 1) {
  if (attempt > fixState.maxAttempts) {
    return {
      success: false,
      error: 'Max attempts reached',
      attempts: attempt - 1,
      attemptsHistory: error.attemptsHistory || []
    };
  }
  
  try {
    // 1. Запрос fix у Qwen (заглушка для Phase 5)
    const fix = await callQwenForFix(error);
    
    // 2. Применение fix (заглушка)
    const applied = await applyFix(runId, fix);
    
    if (applied) {
      // 3. Повторный запуск
      const workItemId = error.context?.workItemId;
      if (workItemId) {
        await api.runWorkItem(workItemId);
        
        return {
          success: true,
          attempts: attempt,
          attemptsHistory: [
            ...(error.attemptsHistory || []),
            { attempt, success: true, message: 'Fix applied and restarted' }
          ]
        };
      }
    }
    
  } catch (fixError) {
    console.error('[AutoFix] Fix error:', fixError);
  }
  
  // Запись попытки
  const newHistory = [
    ...(error.attemptsHistory || []),
    { attempt, success: false, message: `Attempt ${attempt} failed` }
  ];
  
  // Следующая попытка
  return await autoFixFailedRun(runId, { ...error, attemptsHistory: newHistory }, attempt + 1);
}

/**
 * Вызов Qwen для получения fix
 * TODO: Интеграция с backend для Qwen API
 */
async function callQwenForFix(error) {
  // Заглушка для Phase 5
  // В реальности: POST /api/qwen/fix с контекстом ошибки
  
  console.log('[AutoFix] Calling Qwen for fix:', error.type, error.message);
  
  // Пример ответа:
  return {
    suggestion: `Fix ${error.type}: ${error.message}`,
    files: error.context?.files || [],
    changes: []
  };
}

/**
 * Применение fix
 * TODO: Реальное применение изменений
 */
async function applyFix(runId, fix) {
  // Заглушка для Phase 5
  // В реальности: применение изменений через Forge
  
  console.log('[AutoFix] Applying fix:', fix);
  
  // Симуляция успеха/неудачи
  return Math.random() > 0.3; // 70% успеха
}

/**
 * Эскалация пользователю
 * @param {Object} run - Объект run
 * @param {Object} result - Результат авто-исправления
 */
function escalateToUser(run, result) {
  console.log('[AutoFix] Escalating to user:', run.id, result);
  
  // Показываем EscalationView modal
  if (window.showEscalationModal) {
    window.showEscalationModal(run, result);
  }
  
  // Уведомление пользователя
  if (window.showFactoryToast) {
    window.showFactoryToast('Требуется ваше внимание', 'err');
  }
}

/**
 * Пометить run как обработанный
 */
function markRunAsHandled(runId) {
  fixState.handlingRuns.add(runId);
  setTimeout(() => {
    fixState.handlingRuns.delete(runId);
  }, 60000); // Удаляем через 1 минуту
}

/**
 * Глобальные функции для использования в UI
 */
window.monitorForErrors = monitorForErrors;
window.autoFixFailedRun = autoFixFailedRun;
window.markRunAsHandled = markRunAsHandled;
