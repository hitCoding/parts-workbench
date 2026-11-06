<script setup lang="ts">
import {ref} from 'vue';
import {Layers,LockKeyhole} from '@lucide/vue';
import {api} from './api';
import type {User} from './types';
const props=defineProps<{setup:boolean;tokenRequired:boolean}>();
const emit=defineEmits<{ready:[user:User]}>();
const username=ref(''),password=ref(''),displayName=ref(''),setupToken=ref(''),error=ref(''),busy=ref(false);
async function submit(){busy.value=true;error.value='';try{const result=await api<{user:User}>(props.setup?'/auth/setup':'/auth/login','POST',{username:username.value,password:password.value,display_name:displayName.value,setup_token:setupToken.value});password.value='';emit('ready',result.user);}catch(e){error.value=String(e instanceof Error?e.message:e);}finally{busy.value=false;}}
</script>
<template>
 <main class="auth-screen"><section class="auth-intro"><div class="auth-brand"><Layers :size="32"/> 料格</div><div><span class="eyebrow">YOUR PARTS, IN PLACE.</span><h1>从一颗元件，<br>到下一个作品。</h1><p>收纳、核料、采购、领料。<br>让硬件工作室的每一步井然有序。</p></div><p class="auth-footer">本地存储 · SQLite · 你掌握自己的数据</p></section><section class="auth-form"><div class="settings-icon"><LockKeyhole :size="24"/></div><h2>{{setup?'设置你的工作台':'欢迎回到料格'}}</h2><p class="subtle">{{setup?'创建第一个管理员账号，已有物料与盒子会完整保留。':'使用管理员为你创建的账号登录。'}}</p><form @submit.prevent="submit"><label>用户名<input v-model="username" required autocomplete="username" minlength="3" maxlength="40" pattern="[A-Za-z0-9_.-]+" placeholder="字母、数字或 . _ -"></label><label v-if="setup">显示名称<input v-model="displayName" maxlength="80" placeholder="例如 我的硬件工作室"></label><label>密码<input v-model="password" type="password" :autocomplete="setup?'new-password':'current-password'" :minlength="setup?10:undefined" maxlength="128" required :placeholder="setup?'至少 10 个字符':'输入密码'"></label><label v-if="setup&&tokenRequired">初始化口令<input v-model="setupToken" type="password" required placeholder="服务器 PARTS_SETUP_TOKEN"></label><p v-if="error" class="error-banner" role="alert">{{error}}</p><button class="primary full" :disabled="busy">{{busy?'正在处理…':setup?'创建管理员并进入':'登录工作台'}}</button></form><p class="subtle">{{setup?'管理员可以添加编辑者和只读成员。':'账号权限由服务器验证，库存操作会留下审计记录。'}}</p></section></main>
</template>
