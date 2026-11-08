<script setup lang="ts">
import {nextTick,onBeforeUnmount,ref} from 'vue';
import {Camera,ScanLine,Image,Square} from '@lucide/vue';
import type {IScannerControls} from '@zxing/browser';
const emit=defineEmits<{resolve:[code:string]}>();
const code=ref(''),camera=ref(false),video=ref<HTMLVideoElement>(),error=ref(''),busy=ref(false);
const photoInput=ref<HTMLInputElement>();
let controls:IScannerControls|undefined,stopped=false;
function stop(){stopped=true;controls?.stop();controls=undefined;camera.value=false;}
function takePhoto(){if(busy.value)return;stop();error.value='';photoInput.value?.click();}
async function start(){if(busy.value)return;busy.value=true;error.value='';stopped=false;camera.value=true;await nextTick();try{if(!navigator.mediaDevices?.getUserMedia)throw new Error('实时扫码需要 HTTPS 或 localhost。局域网 HTTP 请使用「拍照识码」或「识别码图」。');const {BrowserMultiFormatReader}=await import('@zxing/browser');const reader=new BrowserMultiFormatReader();const active=await reader.decodeFromVideoDevice(undefined,video.value!,result=>{if(result&&!stopped){const value=result.getText();stop();emit('resolve',value);}});if(stopped)active.stop();else controls=active;}catch(e){stop();error.value=e instanceof Error?e.message:String(e);}finally{busy.value=false;}}
async function imageScan(event:Event){const input=event.target as HTMLInputElement,file=input.files?.[0];if(!file)return;stop();busy.value=true;error.value='';const url=URL.createObjectURL(file);try{const {BrowserMultiFormatReader}=await import('@zxing/browser');const result=await new BrowserMultiFormatReader().decodeFromImageUrl(url);emit('resolve',result.getText());}catch{error.value='没有识别到清晰的二维码或条码，请裁剪后重试。';}finally{URL.revokeObjectURL(url);busy.value=false;input.value='';}}
onBeforeUnmount(stop);
</script>
<template>
 <section class="panel settings-card">
  <div class="settings-icon"><ScanLine :size="25"/></div>
  <h2>扫码即定位</h2>
  <p>扫描料格标签直达盒子、格子或器件。也支持商城编号和扫码枪输入。</p>
  <form class="scan-input" @submit.prevent="emit('resolve',code)">
   <input v-model="code" aria-label="扫描编码" placeholder="R01 / A1、商城编号或标签编码" required>
   <button class="primary">定位</button>
  </form>
  <div class="row">
   <button type="button" class="secondary" :disabled="busy" @click="takePhoto"><Camera :size="15"/>拍照识码</button>
   <input ref="photoInput" type="file" hidden accept="image/*" capture="environment" aria-label="拍照识码" :disabled="busy" @change="imageScan">
   <label class="button secondary"><Image :size="15"/>识别码图<input type="file" hidden accept="image/*" :disabled="busy" @change="imageScan"></label>
   <button v-if="!camera" type="button" class="secondary" :disabled="busy" @click="start"><ScanLine :size="15"/>实时扫码</button>
   <button v-else type="button" class="secondary" @click="stop"><Square :size="15"/>停止相机</button>
  </div>
  <video v-if="camera" ref="video" class="scan-video" muted playsinline></video>
  <p v-if="busy&&!camera" class="subtle" role="status">正在识别图片…</p>
  <p v-if="error" class="red-text" role="alert">{{error}}</p>
  <p class="subtle">手机可拍照后识码，也可选择已有图片；实时扫码需 HTTPS 或本机访问。图片识别在浏览器内完成。</p>
 </section>
</template>
