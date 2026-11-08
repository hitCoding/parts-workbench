import {test} from 'node:test';
import assert from 'node:assert/strict';
import {allocateReceiptSlots,boxCategories} from '../src/receiptAllocation.ts';
const box=(id,name,count=2)=>({id,code:id,name,slots:Array.from({length:count},(_,col_no)=>({id:`${id}-${col_no}`,box_id:id,row_no:0,col_no}))});
const part=(id,category,locations=[])=>({id,category,locations});
const row=(id,part_id,slot_id='',selected=true)=>({purchase:{id,part_id},slot_id,selected});

test('automatic allocation respects categories, physical order and selection',()=>{
 const boxes=[box('R02','贴片电阻'),box('C01','贴片电容'),box('R01','电阻',1)];
 const result=allocateReceiptSlots(boxes,[part('r','电阻'),part('c','电容')],[row('a','r'),row('b','c'),row('c','r'),row('d','r','',false)]);
 assert.deepEqual(result.assignments,{a:'R01-0',b:'C01-0',c:'R02-0'});
 assert.deepEqual(result.unassigned,[]);
});
test('prefer exact stock within matching boxes, never another component or uncertain stock',()=>{
 const boxes=[box('R01','电阻',4),box('C01','电容')];
 const parts=[part('r','电阻',[{slot_id:'R01-3',part_id:'r',mode:'exact'},{slot_id:'R01-2',part_id:'r',mode:'estimated'},{slot_id:'C01-0',part_id:'r',mode:'exact'}]),part('other','电阻',[{slot_id:'R01-0',part_id:'other',mode:'exact'}])];
 const result=allocateReceiptSlots(boxes,parts,[row('a','r'),row('b','r'),row('c','r')]);
 assert.deepEqual(result.assignments,{a:'R01-3',b:'R01-1'});
 assert.equal(result.unassigned[0].id,'c');
});
test('keep manual positions including unchecked rows, do not mutate input or double assign',()=>{
 const rows=[row('manual','r','R01-0',false),row('a','r'),row('b','r')];
 const before=JSON.stringify(rows);
 const result=allocateReceiptSlots([box('R01','电阻')],[part('r','电阻')],rows);
 assert.deepEqual(result.assignments,{a:'R01-1'});
 assert.equal(result.unassigned.length,1);
 assert.equal(JSON.stringify(rows),before);
});
test('missing category boxes stay unassigned rather than spilling into miscellaneous boxes',()=>{
 const result=allocateReceiptSlots([box('Z01','杂料'),box('R01','电阻')],[part('c','电容')],[row('a','c'),row('b','missing')]);
 assert.deepEqual(result.assignments,{});
 assert.equal(result.unassigned.length,2);
 assert.match(result.unassigned[0].reason,/电容/);
});
test('explicit box selection overrides category and is repeatable',()=>{
 const boxes=[box('Z01','杂料')],parts=[part('c','电容')],rows=[row('a','c')];
 const first=allocateReceiptSlots(boxes,parts,rows,'Z01');
 assert.deepEqual(first.assignments,{a:'Z01-0'});
 rows[0].slot_id=first.assignments.a;
 assert.deepEqual(allocateReceiptSlots(boxes,parts,rows,'Z01'),{assignments:{},unassigned:[]});
});
test('box classifications come from names, not box code',()=>{
 assert.deepEqual(boxCategories(box('C01','贴片电阻')),['电阻']);
 assert.deepEqual(boxCategories(box('R01','杂料')),[]);
 assert.deepEqual(boxCategories(box('U01','芯片 / MCU')),['芯片','MCU']);
});
