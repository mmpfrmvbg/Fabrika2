/**
 * Factory OS — Autonomous Auto-Queue Module
 * Управление очередью задач для автономного выполнения
 */

import { store } from '../state/store.js';

/**
 * Построение умной очереди задач для Vision
 * @param {string} visionId - ID Vision
 * @returns {Object} Структура очереди
 */
export function buildAutoQueue(visionId) {
  const workItems = store?.state?.workItems || [];
  
  // 1. Получаем все атомы Vision
  const atoms = getAllAtomsForVision(visionId, workItems);
  
  // 2. Сортировка по зависимостям (topological sort)
  const sorted = topologicalSort(atoms, workItems);
  
  // 3. Группировка по уровням параллелизма
  const levels = groupByDependencies(sorted, workItems);
  
  // 4. Формируем структуру очереди
  return {
    visionId,
    current: levels[0] || [],
    queued: levels.slice(1) || [],
    total: atoms.length,
    completed: atoms.filter(a => a.status === 'done').length,
    inProgress: atoms.filter(a => a.status === 'in_progress').length,
    failed: atoms.filter(a => a.status === 'failed' || a.status === 'review_rejected').length
  };
}

/**
 * Получение всех атомов для Vision
 */
function getAllAtomsForVision(visionId, workItems) {
  const atoms = [];
  const queue = [visionId];
  
  while (queue.length > 0) {
    const id = queue.shift();
    const children = workItems.filter(w => w.parent_id === id);
    
    for (const child of children) {
      if (child.kind === 'atom') {
        atoms.push(child);
      } else {
        queue.push(child.id);
      }
    }
  }
  
  return atoms;
}

/**
 * Топологическая сортировка атомов
 * Сортирует так чтобы зависимости выполнялись первыми
 */
function topologicalSort(atoms, workItems) {
  // Эвристика порядка:
  // 1) in_progress/in_review (уже стартовали),
  // 2) ready/planned/draft с учётом количества зависимостей,
  // 3) done/archived.
  const dependencyWeight = (atom) => getDependencyIds(atom).length;
  
  const ready = atoms.filter(a => 
    a.status === 'ready_for_work' || 
    a.status === 'planned' ||
    a.status === 'draft'
  );
  
  const inProgress = atoms.filter(a => 
    a.status === 'in_progress' || 
    a.status === 'in_review'
  );
  
  const done = atoms.filter(a => 
    a.status === 'done' || 
    a.status === 'archived'
  );
  
  const readySorted = [...ready].sort((a, b) => dependencyWeight(a) - dependencyWeight(b));
  return [...inProgress, ...readySorted, ...done];
}

/**
 * Группировка по уровням для параллельного выполнения
 */
function groupByDependencies(sortedAtoms, workItems) {
  const levels = [];
  const processed = new Set();
  
  // Уровень 0: готовые к выполнению
  levels[0] = sortedAtoms.filter(a => 
    a.status === 'ready_for_work' && !processed.has(a.id)
  );
  levels[0].forEach(a => processed.add(a.id));
  
  // Последующие уровни: зависимые от предыдущих
  let levelIndex = 1;
  while (processed.size < sortedAtoms.length) {
    const nextLevel = sortedAtoms.filter(a => 
      !processed.has(a.id) &&
      areDependenciesMet(a, processed, workItems)
    );
    
    if (nextLevel.length === 0) {
      // Нет доступных задач — возможно блокировка
      const remaining = sortedAtoms.filter(a => !processed.has(a.id));
      if (remaining.length > 0) {
        levels[levelIndex] = remaining;
        remaining.forEach(a => processed.add(a.id));
      }
      break;
    }
    
    levels[levelIndex] = nextLevel;
    nextLevel.forEach(a => processed.add(a.id));
    levelIndex++;
  }
  
  return levels;
}

/**
 * Проверка выполнены ли зависимости задачи
 */
function areDependenciesMet(atom, processedIds, workItems) {
  // Если явно указаны зависимости — учитываем их в первую очередь.
  const dependencyIds = getDependencyIds(atom);
  if (dependencyIds.length > 0) {
    return dependencyIds.every((depId) => processedIds.has(depId));
  }
  
  const parent = workItems.find(w => w.id === atom.parent_id);
  if (!parent) return true;
  
  return processedIds.has(parent.id);
}

/**
 * Извлекает список зависимостей atom из поддерживаемых полей.
 * Поддержка:
 * - atom.dependencies: string[]
 * - atom.depends_on: string[]
 * - atom.metadata (JSON): { dependencies: string[] } | { depends_on: string[] }
 */
function getDependencyIds(atom) {
  if (!atom || typeof atom !== 'object') return [];
  const direct = []
    .concat(Array.isArray(atom.dependencies) ? atom.dependencies : [])
    .concat(Array.isArray(atom.depends_on) ? atom.depends_on : []);

  let metaDeps = [];
  const rawMeta = atom.metadata;
  if (rawMeta) {
    try {
      const parsed = typeof rawMeta === 'string' ? JSON.parse(rawMeta) : rawMeta;
      metaDeps = []
        .concat(Array.isArray(parsed?.dependencies) ? parsed.dependencies : [])
        .concat(Array.isArray(parsed?.depends_on) ? parsed.depends_on : []);
    } catch (_e) {
      // ignore invalid metadata JSON
    }
  }

  return [...new Set([...direct, ...metaDeps].map(String).filter(Boolean))];
}

/**
 * Получение следующего атома для запуска
 */
export function getNextAtomToLaunch(queue) {
  if (!queue || !queue.current || queue.current.length === 0) {
    return null;
  }
  
  // Первый готовый атом из текущей очереди
  const readyAtom = queue.current.find(a => 
    a.status === 'ready_for_work'
  );
  
  return readyAtom || queue.current[0];
}

/**
 * Обновление очереди после завершения атома
 */
export function updateQueueAfterCompletion(queue, completedAtomId) {
  // Удаляем завершённый атом из current
  const newCurrent = queue.current.filter(a => a.id !== completedAtomId);
  
  // Если current пуст, берём следующий уровень
  if (newCurrent.length === 0 && queue.queued.length > 0) {
    return {
      ...queue,
      current: queue.queued[0] || [],
      queued: queue.queued.slice(1)
    };
  }
  
  return {
    ...queue,
    current: newCurrent
  };
}

/**
 * Глобальные функции для использования в UI
 */
window.buildAutoQueue = buildAutoQueue;
window.getNextAtomToLaunch = getNextAtomToLaunch;
window.updateQueueAfterCompletion = updateQueueAfterCompletion;
