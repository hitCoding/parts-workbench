<script setup lang="ts">
import {computed,onMounted,ref} from 'vue';
import DialogShell from './DialogShell.vue';
import {api} from './api';
import {cleanProductName} from './productName';
import type {State,Purchase} from './types';
const props=defineProps<{state:State;ids:string[]}>();
const emit=defineEmits<{close:[];changed:[]}>();
interface Allocation{slot_id:string;quantity:number}
interface Row{id:string;revision:string;quantity:number;received:number;allocations:Allocation[];purchase:Purchase}
const rows=ref<Row[]>([]),busy=ref(true),error=ref(''),reason=ref('');
const initial=ref('');
const slots=computed(()=>props.state.boxes.flatMap(b=>b.slots.map(s=>({...s,label:`${b.code} / ${String.fromCharCode(65+s.row_no)}${s.col_no+1}`}))));
function stockAt(id:string){return props.state.parts.flatMap(p=>p.locations).find(s=>s.slot_id===id);}
function slotsFor(row:Row){return slots.value.filter(s=>!stockAt(s.id)||stockAt(s.id)?.part_id===row.purchase.part_id||row.allocations.some(a=>a.slot_id===s.id));}
function total(row:Row){return row.allocations.reduce((n,a)=>n+(Number(a.quantity)||0),0);}
function payload(){return {items:rows.value.map(({id,revision,allocations})=>({id,revision,allocations})),reason:reason.value};}
const changed=computed(()=>JSON.stringify(payload().items)!==initial.value);
const valid=computed(()=>rows.value.length>0&&rows.value.every(r=>total(r)<=r.quantity&&r.allocations.every(a=>a.slot_id&&Number.isInteger(a.quantity)&&a.quantity>0)&&new Set(r.allocations.map(a=>a.slot_id)).size===r.allocations.length));
onMounted(async()=>{try{const result=await api<{items:Omit<Row,'purchase'>[]}>('/purchases/receipts/inspect','POST',{ids:props.ids});rows.value=result.items.map(r=>({...r,purchase:props.state.purchases.find(p=>p.id===r.id)!}));initial.value=JSON.stringify(payload().items);}catch(e){error.value=e instanceof Error?e.message:String(e);}finally{busy.value=false;}});
async function save(){if(busy.value)return;busy.value=true;error.value='';try{await api<{count:number}>('/purchases/receipts/correct','POST',payload());emit('changed');emit('close');}catch(e){error.value=e instanceof Error?e.message:String(e);}finally{busy.value=false;}}
</script>
<template>
 <DialogShell title="修改已验收记录" wide :busy="busy" @close="emit('close')">
  <div v-if="error" class="error-banner" role="alert">{{error}}</div>
  <p class="form-hint">填写每个格子修改后的累计入库数量。保存时仅调整与原验收的差额；删除某个位置即撤回该位置的验收数量。</p>
  <div class="callout warm">修改前自动备份，原流水保留并追加修正记录。若库存已领用、移位或被项目占用，无法扣减时会提示处理。</div>
  <div v-if="busy&&!rows.length" class="subtle" role="status">正在读取原验收记录…</div>
  <div v-if="rows.length" class="table-wrap order-preview receipt-editor"><table><thead><tr><th>序号</th><th>商品名称 / 型号</th><th>原入库 → 修改后</th><th>修改后存放格子与数量</th></tr></thead><tbody>
   <tr v-for="(row,index) in rows" :key="row.id"><td>{{index+1}}</td><td><strong>{{cleanProductName(row.purchase.product_name||row.purchase.part_name)}}</strong><small>{{row.purchase.part_name}} · {{row.purchase.package}}</small></td><td><span class="mono" :class="total(row)>row.quantity?'red-text':''">{{row.received}} → {{total(row)}}</span><small>采购 {{row.quantity}} 个</small></td><td>
    <div v-for="(a,i) in row.allocations" :key="i" class="allocation-row"><select v-model="a.slot_id" :disabled="busy" :aria-label="'第'+(index+1)+'项 位置'+(i+1)"><option value="">选择格子</option><option v-for="s in slotsFor(row)" :value="s.id" :key="s.id">{{s.label}}</option></select><input v-model.number="a.quantity" :disabled="busy" type="number" min="1" step="1" :aria-label="'第'+(index+1)+'项 数量'+(i+1)"><button class="text-button red-text" :disabled="busy" :aria-label="'删除第'+(index+1)+'项位置'+(i+1)" @click="row.allocations.splice(i,1)">移除</button></div>
    <button class="text-button" :disabled="busy" @click="row.allocations.push({slot_id:'',quantity:1})">＋ 添加位置</button>
   </td></tr>
  </tbody></table></div>
  <label>修改原因<input v-model="reason" :disabled="busy" maxlength="500" required placeholder="例如：验收数量录错，或实物改放其他格子"></label>
  <div class="modal-footer"><span class="subtle">{{rows.length}} 项 · 库存按差额调整</span><button class="secondary" :disabled="busy" @click="emit('close')">取消</button><button class="primary" :disabled="busy||!valid||!changed||!reason.trim()" @click="save">保存验收修改</button></div>
 </DialogShell>
</template>
