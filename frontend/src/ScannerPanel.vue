<script setup lang="ts">
import {nextTick,onBeforeUnmount,ref} from 'vue';
import {Camera,ScanLine,Image,Square} from '@lucide/vue';
import type {IScannerControls} from '@zxing/browser';
const emit=defineEmits<{resolve:[code:string]}>();
const code=ref(''),camera=ref(false),video=ref<HTMLVideoElement>(),error=ref(''),busy=ref(false);
let controls:IScannerControls|undefined,stopped=false;
function stop(){stopped=true;controls?.stop();controls=undefined;camera.value=false;}
async function start(){if(busy.value)return;busy.value=true;error.value='';stopped=false;camera.value=true;await nextTick();try{if(!navigator.mediaDevices?.getUserMedia)throw new Error('相机需要 HTTPS 或 localhost。也可以选择二维码图片或使用扫码枪。');const {BrowserMultiFormatReader}=await import('@zxing/browser');const reader=new BrowserMultiFormatReader();const active=await reader.decodeFromVideoDevice(undefined,video.value!,result=>{if(result&&!stopped){const value=result.getText();stop();emit('resolve',value);}});if(stopped)active.stop();else controls=active;}catch(e){stop();error.value=e instanceof Error?e.message:String(e);}finally{busy.value=false;}}
async function imageScan(event:Event){const input=event.target as HTMLInputElement,file=input.files?.[0];if(!file)return;stop();busy.value=true;error.value='';const url=URL.createObjectURL(file);try{const {BrowserMultiFormatReader}=await import('@zxing/browser');const result=await new BrowserMultiFormatReader().decodeFromImageUrl(url);emit('resolve',result.getText());}catch{error.value='没有识别到清晰的二维码或条码，请裁剪后重试。';}finally{URL.revokeObjectURL(url);busy.value=false;input.value='';}}
onBeforeUnmount(stop);
</script>
<template><section class="panel settings-card"><div class="settings-icon"><ScanLine :size="25"/></div><h2>扫码即定位</h2><p>扫描料格标签直达盒子、格子或器件。也支持商城编号和扫码枪输入。</p><form class="scan-input" @submit.prevent="emit('resolve',code)"><input v-model="code" aria-label="扫描编码" placeholder="R01 / A1、商城编号或标签编码" required><button class="primary">定位</button></form><div class="row"><button v-if="!camera" class="secondary" :disabled="busy" @click="start"><Camera :size="15"/>打开相机</button><button v-else class="secondary" @click="stop"><Square :size="15"/>停止相机</button><label class="button secondary"><Image :size="15"/>识别码图<input type="file" hidden accept="image/*" :disabled="busy" @change="imageScan"></label></div><video v-if="camera" ref="video" class="scan-video" muted playsinline></video><p v-if="error" class="red-text" role="alert">{{error}}</p><p class="subtle">图片识码在浏览器内完成。局域网手机摄像头需使用 HTTPS。</p></section></template>
