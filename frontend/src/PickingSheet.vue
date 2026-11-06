<script setup lang="ts">
import {nextTick,onMounted,ref} from 'vue';
import {Download,Printer,RefreshCw,LayoutGrid} from '@lucide/vue';
import DialogShell from './DialogShell.vue';
import {api} from './api';
import {cleanProductName} from './productName';
interface PickItem{id:string;line_no:number;name:string;model:string;product_name:string;package:string;designators:string;demand:number;quantity:number;location:string;notes:string;area?:string}
interface Plan{name:string;boards:number;status:string;rows:PickItem[];pending:PickItem[];external:PickItem[];omitted:PickItem[];pick_lines:number;pick_quantity:number}
const props=defineProps<{projectId:string}>();
const emit=defineEmits<{close:[]}>();
const plan=ref<Plan|null>(null),busy=ref(false),error=ref(''),confirmed=ref(false);
const heading=ref<HTMLElement>();
async function confirm(){confirmed.value=true;await nextTick();heading.value?.closest('[role=dialog]')?.scrollTo({top:0});}
async function load(){busy.value=true;error.value='';try{plan.value=await api<Plan>(`/projects/${props.projectId}/picking`);confirmed.value=!plan.value.pending.length;}catch(e){error.value=String(e);}finally{busy.value=false;}}
onMounted(load);
</script>
<template>
 <DialogShell title="备料位置清单" wide scrollable :busy="busy" @close="emit('close')">
  <p v-if="error" class="error-banner" role="alert">{{error}}</p>
  <p v-if="busy">正在按当前位置生成清单…</p>
  <template v-if="plan">
   <h3 ref="heading">{{plan.name}} · {{plan.boards}} 块</h3>
   <div class="bom-summary"><span>{{plan.pick_lines}} 项可从库存取料 · {{plan.pick_quantity}} 颗</span><span class="warm-text">{{plan.pending.length}} 项待处理</span><span>{{plan.external.length}} 项外部自备</span><span>{{plan.omitted.length}} 项不贴装</span></div>
   <div class="callout">本清单用于找料和查看位置，不会占用或扣减库存。数量按当前精确可用库存分配，同一器件的重复用量不会重复计算。</div>
   <div v-if="plan.pending.length" class="callout warm">有 {{plan.pending.length}} 项未匹配、缺料或待盘点。可以先输出已知位置，下列待处理项会一并保留在清单中。</div>
   <details v-if="plan.pending.length" class="picking-section" :open="!confirmed"><summary>待处理项（{{plan.pending.length}} 项，点击展开）</summary><div class="table-wrap"><table><thead><tr><th>器件 / 位号</th><th>需求</th><th>原因 / 参考位置</th></tr></thead><tbody><tr v-for="item in plan.pending" :key="item.id"><td>{{item.name}}<small>{{item.designators}}</small></td><td>{{item.demand}}</td><td>{{item.notes}}<small>{{item.location}}</small></td></tr></tbody></table></div></details>
   <template v-if="confirmed">
    <section class="picking-section"><h3>库存位置 · 按盒号和格子排列</h3><p v-if="!plan.rows.length" class="subtle">暂无可从精确库存取料的项目，请查看外部自备和待处理项。</p><div v-else class="table-wrap"><table><thead><tr><th>位置</th><th>器件 / 型号</th><th>BOM 行 / 位号</th><th>本格建议取料</th><th>备注</th></tr></thead><tbody><tr v-for="(item,index) in plan.rows" :key="index"><td><strong>{{item.location}}</strong><small>{{item.area}}</small></td><td>{{cleanProductName(item.product_name)||item.name}}<small>{{item.model}} · {{item.package}}</small></td><td>{{item.line_no}}<small>{{item.designators}}</small></td><td class="mono">{{item.quantity}}<small>本行共需 {{item.demand}}</small></td><td>{{item.notes||'—'}}</td></tr></tbody></table></div></section>
    <section v-if="plan.external.length" class="picking-section"><h3>外部自备 / 样品 · 不扣库存</h3><div class="table-wrap"><table><thead><tr><th>器件 / 位号</th><th>需自备</th><th>来源 / 位置说明</th></tr></thead><tbody><tr v-for="item in plan.external" :key="item.id"><td>{{item.name}}<small>{{item.designators}}</small></td><td>{{item.demand}}</td><td>{{item.notes}}</td></tr></tbody></table></div></section>
    <section v-if="plan.omitted.length" class="picking-section"><h3>不贴装 · 不取料</h3><p v-for="item in plan.omitted" :key="item.id" class="subtle">第 {{item.line_no}} 行 · {{item.name}} · {{item.designators||'未填写位号'}}</p></section>
   </template>
   <div class="modal-footer"><button class="secondary" :disabled="busy" @click="load"><RefreshCw :size="15"/>更新清单</button><button v-if="!confirmed" class="primary" @click="confirm">已知悉，继续生成位置清单</button><template v-else><a class="button secondary" :href="`/api/projects/${projectId}/picking.csv`"><Download :size="15"/>导出 CSV</a><a class="button secondary" :href="`/api/projects/${projectId}/picking/print`" target="_blank" rel="noopener"><Printer :size="15"/>打印明细表</a><a class="button primary" :href="`/api/projects/${projectId}/picking/map`" target="_blank" rel="noopener"><LayoutGrid :size="15"/>打印位置图</a></template></div>
  </template>
 </DialogShell>
</template>
<style scoped>.picking-section{margin:20px 0}.picking-section h3{margin-bottom:10px}.picking-section summary{cursor:pointer;font-weight:600;margin-bottom:10px}.bom-summary{margin:14px 0;padding:12px;flex-wrap:wrap}.callout{margin:12px 0}.table-wrap{max-height:420px;overflow:auto}table{min-width:650px}.modal-footer{flex-wrap:wrap}</style>
