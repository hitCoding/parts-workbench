<script setup lang="ts">
import {nextTick,onBeforeUnmount,onMounted,ref} from 'vue';
import {X} from '@lucide/vue';
const props=defineProps<{title:string;wide?:boolean;busy?:boolean;scrollable?:boolean}>();
const emit=defineEmits<{close:[]}>();
const el=ref<HTMLElement>();let previous:HTMLElement|null=null;
function close(){if(!props.busy)emit('close');}
function key(event:KeyboardEvent){if(event.key==='Escape'){event.preventDefault();close();return;}if(event.key!=='Tab')return;const elements=Array.from(el.value?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled):not([type=hidden]),select:not(:disabled),textarea:not(:disabled),a[href]')||[]).filter(x=>x.getClientRects().length>0);const first=elements[0],last=elements[elements.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}
onMounted(async()=>{previous=document.activeElement as HTMLElement;await nextTick();el.value?.querySelector<HTMLElement>('input:not([type=hidden]),select,button')?.focus();});
onBeforeUnmount(()=>previous?.focus());
</script>
<template><div class="modal-backdrop" @click.self="close"><section ref="el" class="modal intake-dialog" :class="{wide,scrollable}" role="dialog" aria-modal="true" :aria-label="title" @keydown="key"><div class="modal-header"><h2>{{title}}</h2><button class="icon-button" aria-label="关闭窗口" :disabled="busy" @click="close"><X :size="20"/></button></div><slot/></section></div></template>
<style scoped>.intake-dialog.scrollable{display:block;max-height:90vh;overflow-y:auto}</style>
