import assert from 'node:assert/strict';

global.window = { FACTORY_API_BASE: 'http://127.0.0.1:8000', FACTORY_API_KEY: 'test' };
global.document = {
  documentElement: {
    getAttribute: () => null
  }
};

const { api } = await import('../../../../static/js/api/client.js');
const { store } = await import('../../../../static/js/state/store.js');

const initialState = JSON.parse(JSON.stringify(store.state));

function resetStore() {
  Object.keys(store.state).forEach((k) => delete store.state[k]);
  Object.assign(store.state, JSON.parse(JSON.stringify(initialState)));
}

async function testStateMutations() {
  resetStore();
  store.addWorkItem({ id: 'a1', status: 'draft' });
  assert.equal(store.state.workItems.length, 1);

  store.updateWorkItemStatus('a1', 'done');
  assert.equal(store.state.workItems[0].status, 'done');

  store.setFilter('status', 'done');
  assert.equal(store.state.treeFilters.status, 'done');
}

async function testApiErrorHandling() {
  resetStore();
  const original = api.getWorkItems;
  api.getWorkItems = async () => {
    throw new Error('network failed');
  };

  await store.loadWorkItems();
  assert.equal(store.state.apiError, 'network failed');

  api.getWorkItems = original;
}

async function testPaginationState() {
  resetStore();
  const original = api.getWorkItems;
  api.getWorkItems = async () => ({
    items: [{ id: 'w1' }, { id: 'w2' }],
    total: 5
  });

  await store.loadWorkItems(2, 2);
  assert.equal(store.state.pagination.currentPage, 2);
  assert.equal(store.state.pagination.totalCount, 5);
  assert.equal(store.state.pagination.hasMore, true);

  api.getWorkItems = original;
}

const tests = [
  testStateMutations,
  testApiErrorHandling,
  testPaginationState
];

for (const t of tests) {
  await t();
  console.log(`PASS ${t.name}`);
}

console.log(`All ${tests.length} tests passed.`);
