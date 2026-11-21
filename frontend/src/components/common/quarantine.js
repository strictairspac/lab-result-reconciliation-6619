import {esc} from '../../utils/html.js';
import {QUARANTINE_STATUSES} from '../../constants/enums.js';
export const quarantineStatus=s=>`<span class="status q-${s}">${esc(QUARANTINE_STATUSES[s]||s)}</span>`;
export function quarantinePanel(store,runId,iso){
 const canWrite=store.user.role!=='viewer';
 const qs=store.quarantines||[];
 return `<section class="isolation-block">
  <h3>质控隔离单 <em>视图版本 v${esc(iso?.isolation_version??store.quarantineVersion??0)}</em></h3>
  <p class="muted">按检测项与检测时间窗标记不可采信结果；生效与撤销一次性切换，单次核对只见同一版。隔离不改动原始目标行与备份摘要。</p>
  ${canWrite?`<form id="quarantine-form" class="q-form">
   <label>隔离单标题<input name="title" required minlength="2" maxlength="120" placeholder="如：2026-11-17 拉伸强度质控失效"></label>
   <label>检测项（精确匹配 test_name）<input name="test_name" required maxlength="160" placeholder="如：拉伸强度"></label>
   <div class="duo">
    <label>检测开始时间<input name="measured_from" type="datetime-local" required step="1"></label>
    <label>检测结束时间<input name="measured_to" type="datetime-local" required step="1"></label>
   </div>
   <label>质控原因<input name="reason" maxlength="500" placeholder="设备校准偏差 / 质控超限等"></label>
   <button type="submit">登记隔离单 →</button>
  </form>`:'<p>管理员或操作员可登记、生效与撤销隔离单。</p>'}
  <h3>隔离单台账 <em>${qs.length}</em></h3>
  ${qs.length?`<div class="table-wrap"><table><thead><tr><th>单号 / 标题</th><th>检测项 / 时间窗（UTC）</th><th>状态</th><th>命中</th><th>操作</th></tr></thead><tbody>
   ${qs.map(q=>`<tr>
    <td><b>${esc(q.code)}</b><small>${esc(q.title)}</small>${q.reason?`<small>${esc(q.reason)}</small>`:''}</td>
    <td><b>${esc(q.test_name)}</b><small>${esc(q.measured_from)}<br>→ ${esc(q.measured_to)}</small></td>
    <td>${quarantineStatus(q.status)}</td>
    <td>${Array.isArray(q.affected)?q.affected.length:0} 行</td>
    <td>${canWrite&&q.status==='draft'?`<button class="quiet" data-q-activate="${q.id}">生效</button>`:''}${canWrite&&q.status==='active'?`<button class="quiet" data-q-revoke="${q.id}">撤销</button>`:''}${q.status==='revoked'?'<small>已留痕</small>':''}</td>
   </tr>`).join('')}
  </tbody></table></div>`:'<div class="empty">还没有隔离单。</div>'}
  ${iso?`<h3>本运行受影响结果 <em>${iso.quarantined_count}/${iso.count} 行</em></h3>
   <div class="table-wrap"><table><thead><tr><th>委托号</th><th>检测项</th><th>检测时间</th><th>结果</th><th>隔离标记</th></tr></thead><tbody>
   ${iso.rows.map(x=>`<tr class="${x.quarantined?'row-quarantined':''}">
    <td>${esc(x.row.sample_code)}</td><td>${esc(x.row.test_name)}</td><td><small>${esc(x.row.measured_at)}</small></td>
    <td>${esc(x.row.result_value)} ${esc(x.row.unit||'')}</td>
    <td>${x.quarantined?x.quarantine_refs.map(r=>`<span class="q-mark" title="${esc(r.title)}">⛔ ${esc(r.code)}</span>`).join(' '):'<small>正常</small>'}</td>
   </tr>`).join('')}
   </tbody></table></div>`:''}
 </section>`;
}
