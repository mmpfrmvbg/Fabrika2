/**
 * Factory OS — Autonomous Auto-Decompose Module
 * Автоматическая декомпозиция Vision на Epic → Story → Task → Atom
 */

import { api } from '../api/client.js';

/**
 * Авто-декомпозиция Vision на иерархию задач
 * @param {string} visionId - ID Vision
 * @param {string} title - Заголовок Vision
 * @param {string} description - Описание Vision
 * @returns {Promise<Object>} Декомпозиция
 */
export async function autoDecomposeVision(visionId, title, description) {
  try {
    // 1. Запрос к Qwen API для декомпозиции
    const decomposition = await callQwenDecomposition(title, description);
    
    // 2. Парсинг ответа
    const hierarchy = parseDecomposition(decomposition);
    
    // 3. Создание задач через API
    await createTaskHierarchy(visionId, hierarchy);
    
    // 4. Возвращаем структуру
    return {
      success: true,
      visionId,
      hierarchy,
      totalTasks: countTotalTasks(hierarchy)
    };
    
  } catch (error) {
    console.error('[AutoDecompose] Error:', error);
    return {
      success: false,
      error: error.message,
      visionId
    };
  }
}

/**
 * Вызов Qwen API для декомпозиции
 * TODO: Интеграция с backend для Qwen вызова
 */
async function callQwenDecomposition(title, description) {
  // Заглушка для Phase 3
  // В реальности: POST /api/visions/{id}/decompose
  
  // Пример ответа от Qwen:
  return {
    epics: [
      {
        title: 'Auth Module Refactoring',
        description: 'Основные улучшения auth модуля',
        stories: [
          {
            title: 'Error Handling',
            description: 'Улучшение обработки ошибок',
            tasks: [
              {
                title: 'Add error types',
                description: 'Создать типы ошибок',
                atoms: [
                  {
                    title: 'Create auth/errors.py',
                    description: 'Файл с типами ошибок',
                    files: ['auth/errors.py']
                  },
                  {
                    title: 'Define AuthError classes',
                    description: 'Классы ошибок авторизации',
                    files: ['auth/errors.py']
                  }
                ]
              }
            ]
          }
        ]
      }
    ]
  };
}

/**
 * Парсинг ответа от Qwen
 */
function parseDecomposition(response) {
  // Валидация и нормализация структуры
  if (!response || !response.epics) {
    throw new Error('Invalid decomposition format');
  }
  
  return response;
}

/**
 * Создание иерархии задач через API
 */
async function createTaskHierarchy(parentId, hierarchy) {
  // Создание Epic
  if (hierarchy.epics) {
    for (const epic of hierarchy.epics) {
      const epicData = {
        title: epic.title,
        description: epic.description || '',
        kind: 'epic'
      };
      
      const epicResult = await api.createChild(parentId, epicData);
      const epicId = epicResult.id || epicResult.work_item_id;
      
      // Рекурсивное создание Story → Task → Atom
      await createStoryHierarchy(epicId, epic);
    }
  }
}

async function createStoryHierarchy(parentId, epic) {
  if (epic.stories) {
    for (const story of epic.stories) {
      const storyData = {
        title: story.title,
        description: story.description || '',
        kind: 'story'
      };
      
      const storyResult = await api.createChild(parentId, storyData);
      const storyId = storyResult.id || storyResult.work_item_id;
      
      await createTaskHierarchy(storyId, story);
    }
  }
  
  if (epic.tasks) {
    for (const task of epic.tasks) {
      const taskData = {
        title: task.title,
        description: task.description || '',
        kind: 'task'
      };
      
      const taskResult = await api.createChild(parentId, taskData);
      const taskId = taskResult.id || taskResult.work_item_id;
      
      await createAtomHierarchy(taskId, task);
    }
  }
  
  if (epic.atoms) {
    for (const atom of epic.atoms) {
      const atomData = {
        title: atom.title,
        description: atom.description || '',
        kind: 'atom',
        files: (atom.files || []).map(path => ({ path, intent: 'modify' }))
      };
      
      await api.createChild(parentId, atomData);
    }
  }
}

async function createAtomHierarchy(parentId, task) {
  if (task.atoms) {
    for (const atom of task.atoms) {
      const atomData = {
        title: atom.title,
        description: atom.description || '',
        kind: 'atom',
        files: (atom.files || []).map(path => ({ path, intent: 'modify' }))
      };
      
      await api.createChild(parentId, atomData);
    }
  }
}

/**
 * Подсчёт общего количества задач
 */
function countTotalTasks(hierarchy) {
  let count = 0;
  
  function countRecursive(obj) {
    if (obj.epics) obj.epics.forEach(e => countRecursive(e));
    if (obj.stories) obj.stories.forEach(s => countRecursive(s));
    if (obj.tasks) obj.tasks.forEach(t => countRecursive(t));
    if (obj.atoms) count += obj.atoms.length;
  }
  
  countRecursive(hierarchy);
  return count;
}

/**
 * Глобальная функция для использования в UI
 */
window.autoDecomposeVision = autoDecomposeVision;
