<script setup lang="ts">
import {onMounted,reactive,ref} from 'vue';
import {ScanText,Users,ShieldCheck,RefreshCw,KeyRound} from '@lucide/vue';
import {api} from './api';
import type {User} from './types';
import ScannerPanel from './ScannerPanel.vue';
const props=defineProps<{user:User}>();
const emit=defineEmits<{resolve:[code:string];importCsv:[csv:string,kind:string]}>();
const roles={admin:'管理员',editor:'编辑者',viewer:'只读成员'};
const capabilities=ref<{ocr_engine:string|null;supplier_connected:boolean}|null>(null);
const members=ref<User[]>([]),audit=ref<{id:string;username:string;method:string;path:string;created_at:string}[]>([]);
const error=ref(''),message=ref(''),busy=ref(false),ocrText=ref(''),ocrCsv=ref(''),ocrKind=ref('inventory');
const member=reactive({username:'',display_name:'',password:'',role:'viewer'}),password=reactive({current_password:'',new_password:''});
async function task(fn:()=>Promise<void>){if(busy.value)return;busy.value=true;error.value='';message.value='';try{await fn();}catch(e){error.value=e instanceof Error?e.message:String(e);}finally{busy.value=false;}}
async function load(){capabilities.value=await api('/capabilities');if(props.user.role==='admin'){members.value=await api('/users');audit.value=await api('/audit');}}
onMounted(()=>task(load));
async function recognize(event:Event){const input=event.target as HTMLInputElement,file=input.files?.[0];if(!file)return;await task(async()=>{const body=new FormData();body.append('file',file);const result=await api<{text:string;csv:string}>('/ocr','POST',body);ocrText.value=result.text;ocrCsv.value=result.csv;message.value=result.text?'识别完成，请校对后再导入。':'没有识别到文字，请裁剪图片后重试。';});input.value='';}
async function createMember(){await task(async()=>{await api('/users','POST',member);Object.assign(member,{username:'',display_name:'',password:'',role:'viewer'});await load();message.value='成员已创建，请将账号交给对应使用者。';});}
async function updateMember(u:User){await task(async()=>{await api(`/users/${u.id}`,'PUT',u);await load();message.value='权限已更新，该成员需要重新登录。';});}
async function changePassword(){await task(async()=>{await api('/auth/password','POST',password);password.current_password='';password.new_password='';message.value='密码已更新，其他登录会话已退出。';});}
</script>
<template>
 <div class="page-heading"><div><div class="eyebrow">TOOLS / WORKSPACE</div><h1>让录入更轻，让协作有序。</h1><p>扫码、识字、成员管理，都回到同一份物料数据。</p></div></div>
 <div v-if="error" class="error-banner" role="alert">{{error}}</div><div v-if="message" class="callout" role="status">{{message}}</div><p v-if="busy" class="subtle" role="status">正在处理，请稍候…</p>
 <div class="settings-grid extension-tools">
  <ScannerPanel @resolve="emit('resolve',$event)"/>
  <section class="panel settings-card"><div class="settings-icon"><ScanText :size="25"/></div><h2>标签 / 清单 OCR</h2><p>上传料袋或清单图片，识别文字后人工校对，再进入标准导入预览。</p><span class="badge" :class="capabilities?.ocr_engine?'good':'warm'">{{capabilities?.ocr_engine||'OCR 引擎未安装'}}</span><div class="row"><label v-if="user.role!=='viewer'" class="button secondary">选择图片识别<input type="file" hidden accept="image/png,image/jpeg,image/webp" :disabled="busy||!capabilities?.ocr_engine" @change="recognize"></label></div><details v-if="ocrText"><summary>查看识别原文</summary><pre class="ocr-text">{{ocrText}}</pre></details><div v-if="ocrCsv" class="extension-form"><label>校对为 CSV（补齐类别、数量与位置）<textarea v-model="ocrCsv" rows="8" spellcheck="false" aria-label="OCR 导入表格"></textarea></label><label>导入用途<select v-model="ocrKind"><option value="inventory">物料入库</option><option value="bom">项目 BOM（需增加单板用量列）</option></select></label><button class="primary" :disabled="busy" @click="emit('importCsv',ocrCsv,ocrKind)">进入列映射与预览</button></div><p class="subtle">图片仅传到你的服务器，不使用云端识别。识别结果不会直接修改库存。</p></section>
  <section class="panel settings-card"><div class="settings-icon"><KeyRound :size="25"/></div><h2>账号与密码</h2><p>{{user.display_name}} · {{roles[user.role]}}</p><form class="extension-form" @submit.prevent="changePassword"><label>当前密码<input v-model="password.current_password" type="password" autocomplete="current-password" required></label><label>新密码<input v-model="password.new_password" type="password" minlength="10" maxlength="128" autocomplete="new-password" required placeholder="至少 10 个字符"></label><button class="secondary" :disabled="busy">更新密码</button></form></section>
  <section class="panel settings-card"><div class="settings-icon"><ShieldCheck :size="25"/></div><h2>连接与数据边界</h2><p>当前所有设备连接同一个服务，库存占用和领料在服务端统一检查。</p><dl class="connection-status"><dt>二维码与附件</dt><dd>已启用</dd><dt>数据库版本</dt><dd>v2 · 可恢复 v1 备份</dd><dt>商城实时库存 / 自动下单</dt><dd>未接入供应商接口</dd><dt>断网修改后自动合并</dt><dd>未启用；断网时暂停写入</dd></dl><p class="subtle">供应商接口需要账号与开放 API；采购清单、链接和人工验收可正常使用。</p></section>
 </div>
 <template v-if="user.role==='admin'">
  <section class="panel settings-card members-panel"><div class="row"><Users :size="22"/><h2>工作台成员</h2></div><p class="subtle">管理员可管理账号与备份；编辑者可维护物料；只读成员可查找和扫码。</p><div class="table-wrap"><table><thead><tr><th>账号</th><th>显示名称</th><th>角色</th><th>启用</th><th>操作</th></tr></thead><tbody><tr v-for="u in members" :key="u.id"><td>{{u.username}} <small v-if="u.id===user.id">当前账号</small></td><td><input v-model="u.display_name" :aria-label="u.username+' 显示名称'"></td><td><select v-model="u.role" :aria-label="u.username+' 角色'"><option value="admin">管理员</option><option value="editor">编辑者</option><option value="viewer">只读成员</option></select></td><td><input v-model="u.active" type="checkbox" :true-value="1" :false-value="0" :aria-label="u.username+' 启用'"></td><td><button class="secondary" :disabled="busy" @click="updateMember(u)">保存权限</button></td></tr></tbody></table></div>
   <details class="add-member"><summary>添加新成员</summary><form class="extension-form form-grid" @submit.prevent="createMember"><label>用户名<input v-model="member.username" required minlength="3" maxlength="40" pattern="[A-Za-z0-9_.-]+" autocomplete="off"></label><label>显示名称<input v-model="member.display_name" maxlength="80"></label><label>初始密码<input v-model="member.password" type="password" required minlength="10" maxlength="128" autocomplete="new-password"></label><label>角色<select v-model="member.role"><option value="viewer">只读成员</option><option value="editor">编辑者</option><option value="admin">管理员</option></select></label><div><button class="primary" :disabled="busy">创建成员</button></div></form></details>
  </section>
  <section class="panel settings-card audit-panel"><div class="row"><ShieldCheck :size="22"/><h2>操作审计</h2><button class="text-button" :disabled="busy" @click="task(load)"><RefreshCw :size="14"/>刷新</button></div><p class="subtle">最近 300 次成功提交的操作。库存的具体数量变化见“库存流水”。</p><div class="table-wrap"><table><thead><tr><th>时间</th><th>账号</th><th>动作</th><th>对象</th></tr></thead><tbody><tr v-for="a in audit" :key="a.id"><td>{{new Date(a.created_at).toLocaleString('zh-CN')}}</td><td>{{a.username}}</td><td>{{a.method}}</td><td class="mono">{{a.path}}</td></tr><tr v-if="!audit.length"><td colspan="4">暂时没有写入记录</td></tr></tbody></table></div></section>
 </template>
</template>
