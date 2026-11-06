<script setup lang="ts">
import {ref,watch} from 'vue';
import {Paperclip,FileText,Trash2,Upload} from '@lucide/vue';
import {api} from './api';
const props=defineProps<{partId:string;writable:boolean}>();
interface Attachment{id:string;name:string;mime:string;size:number}
const files=ref<Attachment[]>([]),error=ref(''),busy=ref(false);
let sequence=0;
async function load(){const seq=++sequence;try{const result=await api<Attachment[]>(`/parts/${props.partId}/attachments`);if(seq===sequence)files.value=result;}catch(e){error.value=String(e);}}
watch(()=>props.partId,()=>{files.value=[];error.value='';load();},{immediate:true});
async function upload(event:Event){const input=event.target as HTMLInputElement,file=input.files?.[0];if(!file||busy.value)return;busy.value=true;error.value='';try{const form=new FormData();form.append('file',file);await api(`/parts/${props.partId}/attachments`,'POST',form);await load();}catch(e){error.value=e instanceof Error?e.message:String(e);}finally{busy.value=false;input.value='';}}
async function remove(identity:string){busy.value=true;error.value='';try{await api(`/attachments/${identity}`,'DELETE');await load();}catch(e){error.value=String(e);}finally{busy.value=false;}}
</script>
<template><section class="attachments"><div class="row"><h3><Paperclip :size="14"/>图片与资料</h3><label v-if="writable" class="text-button"><Upload :size="13"/>上传<input type="file" hidden accept="image/png,image/jpeg,image/webp,application/pdf" :disabled="busy" @change="upload"></label></div><p v-if="!files.length" class="subtle">拍下料袋标签，或存一份数据手册。</p><p v-if="error" class="red-text" role="alert">{{error}}</p><div class="attachment-grid"><article v-for="f in files" :key="f.id"><a :href="'/api/attachments/'+f.id" target="_blank" rel="noopener"><img v-if="f.mime.startsWith('image/')" :src="'/api/attachments/'+f.id" :alt="f.name" loading="lazy"><FileText v-else :size="30"/><span>{{f.name}}</span><small>{{Math.ceil(f.size/1024)}} KB</small></a><button v-if="writable" class="icon-button" :aria-label="'移除附件 '+f.name" :disabled="busy" @click="remove(f.id)"><Trash2 :size="13"/></button></article></div><p v-if="writable" class="subtle">单个附件 ≤ 8 MB，最多 12 个。移除前自动备份。</p></section></template>
