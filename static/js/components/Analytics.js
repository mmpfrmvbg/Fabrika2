/**
 * Factory OS — Analytics Component
 * Метрики фабрики: KPI, throughput, stage time
 */

import { store, subscribe } from '../state/store.js';
import { api } from '../api/client.js';

let chartThroughput = null;
let chartStages = null;
let currentPeriod = '24h';

export function AnalyticsComponent(container) {
  let unsubscribe = null;
  
  function subscribeToStore() {
    unsubscribe = subscribe((state) => {
      if (state.activePage === 'analytics' && state.analytics) {
        render(state.analytics);
      }
    });
  }
  
  function render(analytics) {
    if (!container) return;
    
    const kpiContainer = document.getElementById('analytics-kpi-grid');
    const bottleneckContainer = document.getElementById('analytics-bottleneck');
    
    if (kpiContainer) {
      kpiContainer.innerHTML = renderKPIs(analytics);
    }
    
    if (bottleneckContainer) {
      bottleneckContainer.innerHTML = renderBottleneck(analytics);
    }
    
    // Charts
    renderCharts(analytics);
  }
  
  function renderKPIs(data) {
    const v = data.visions || {};
    const a = data.atoms || {};
    const llm = data.llm || {};
    
    const kpis = [
      { 
        label: 'Visions done / total', 
        value: `${v.completed ?? '—'} / ${v.total ?? '—'}`, 
        delta: 'completed / cohort' 
      },
      { 
        label: 'Atoms done / total', 
        value: `${a.completed ?? '—'} / ${a.total ?? '—'}`, 
        delta: 'in period' 
      },
      { 
        label: 'Avg cycle time', 
        value: a.avg_cycle_time_sec != null ? `${a.avg_cycle_time_sec}s` : '—', 
        delta: 'forge → judge' 
      },
      { 
        label: 'First-pass rate', 
        value: fmtPercent(a.first_pass_rate), 
        delta: `retry ${fmtPercent(a.retry_rate)}` 
      },
      { 
        label: 'LLM calls / atom', 
        value: llm.avg_calls_per_atom != null ? String(llm.avg_calls_per_atom) : '—', 
        delta: `tokens in ${llm.total_tokens_in ?? '—'}` 
      },
    ];
    
    return kpis.map(k => `
      <div class="kpi-card">
        <div class="kpi-label">${escapeHtml(k.label)}</div>
        <div class="kpi-value">${escapeHtml(String(k.value))}</div>
        <div class="kpi-delta neutral">${escapeHtml(k.delta)}</div>
      </div>
    `).join('');
  }
  
  function renderBottleneck(data) {
    const st = data.stages || {};
    const forge = st.forge || {};
    const rev = st.review || {};
    const jud = st.judge || {};
    
    const times = {
      forge: Number(forge.avg_duration_sec) || 0,
      review: Number(rev.avg_duration_sec) || 0,
      judge: Number(jud.avg_duration_sec) || 0
    };
    
    let slow = 'forge';
    let maxt = times.forge;
    if (times.review > maxt) { maxt = times.review; slow = 'review'; }
    if (times.judge > maxt) { maxt = times.judge; slow = 'judge'; }
    
    const frSlow = slow === 'forge' ? forge.fail_rate : slow === 'review' ? rev.fail_rate : jud.fail_rate;
    const highFail = Number(frSlow) > 0.15;
    
    return `
      <div>
        <strong style="color:var(--text)">Slowest stage:</strong> 
        <span class="mono">${escapeHtml(slow)}</span> (~${escapeHtml(String(times[slow]))}s avg)
        ${highFail ? `<span style="display:inline-block;margin-left:8px;padding:2px 8px;border-radius:6px;background:var(--error-dim);color:var(--error);font-size:11px">fail rate &gt; 15%</span>` : ''}
      </div>
      <div style="margin-top:8px;font-size:11px">
        Forge fail ${escapeHtml(String(forge.fail_rate))} · 
        Review fail ${escapeHtml(String(rev.fail_rate))} · 
        Judge fail ${escapeHtml(String(jud.fail_rate))}
      </div>
    `;
  }
  
  function renderCharts(data) {
    const elT = document.getElementById('chart-analytics-throughput');
    const elS = document.getElementById('chart-analytics-stages');
    
    if (!elT || !elS || typeof Chart === 'undefined') return;
    
    // Cleanup old charts
    if (chartThroughput) { chartThroughput.destroy(); chartThroughput = null; }
    if (chartStages) { chartStages.destroy(); chartStages = null; }
    
    // Throughput chart
    const tp = Array.isArray(data.throughput) ? data.throughput : [];
    const labels = tp.map(x => String(x.hour || ''));
    const atoms = tp.map(x => Number(x.atoms_completed) || 0);
    const llm = tp.map(x => Number(x.llm_calls) || 0);
    
    chartThroughput = new Chart(elT, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          { 
            label: 'Atoms completed', 
            data: atoms, 
            backgroundColor: 'rgba(90,170,56,0.45)', 
            borderColor: '#5aaa38', 
            borderWidth: 1 
          },
          { 
            label: 'LLM calls', 
            data: llm, 
            backgroundColor: 'rgba(79,152,163,0.35)', 
            borderColor: '#4f98a3', 
            borderWidth: 1 
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { 
            labels: { 
              color: '#72727a', 
              font: { size: 11, family: 'JetBrains Mono' }, 
              boxWidth: 10 
            } 
          },
        },
        scales: {
          x: { 
            ticks: { color: '#3d3d42', maxRotation: 45, minRotation: 0, font: { size: 9 } }, 
            grid: { color: '#1a1a1c' } 
          },
          y: { 
            ticks: { color: '#3d3d42', font: { size: 10 } }, 
            grid: { color: '#1a1a1c' }, 
            beginAtZero: true 
          },
        },
      },
    });
    
    // Stages chart
    const f = Number(st.forge?.avg_duration_sec) || 0;
    const rv = Number(st.review?.avg_duration_sec) || 0;
    const j = Number(st.judge?.avg_duration_sec) || 0;
    
    chartStages = new Chart(elS, {
      type: 'bar',
      data: {
        labels: ['Pipeline'],
        datasets: [
          { 
            label: 'Forge', 
            data: [f], 
            backgroundColor: 'rgba(79,152,163,0.65)', 
            borderWidth: 0 
          },
          { 
            label: 'Review', 
            data: [rv], 
            backgroundColor: 'rgba(168,112,223,0.55)', 
            borderWidth: 0 
          },
          { 
            label: 'Judge', 
            data: [j], 
            backgroundColor: 'rgba(85,145,199,0.55)', 
            borderWidth: 0 
          },
        ],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { 
            position: 'bottom', 
            labels: { 
              color: '#72727a', 
              font: { size: 10, family: 'JetBrains Mono' }, 
              boxWidth: 10 
            } 
          },
        },
        scales: {
          x: {
            stacked: true,
            ticks: { color: '#3d3d42', font: { size: 10 } },
            grid: { color: '#1a1a1c' },
            beginAtZero: true,
            title: { display: true, text: 'seconds', color: '#5a5a60', font: { size: 10 } },
          },
          y: { 
            stacked: true, 
            ticks: { display: false }, 
            grid: { display: false } 
          },
        },
      },
    });
  }
  
  // Глобальные функции
  window.setAnalyticsPeriod = (period, btn) => {
    currentPeriod = period;
    document.querySelectorAll('.analytics-period-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    store.loadAnalytics(period);
  };
  
  subscribeToStore();
  
  return () => {
    if (unsubscribe) unsubscribe();
    if (chartThroughput) chartThroughput.destroy();
    if (chartStages) chartStages.destroy();
  };
}

// Helpers
function fmtPercent(n) {
  if (!Number.isFinite(n)) return '—';
  return (n * 100).toFixed(1) + '%';
}

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
