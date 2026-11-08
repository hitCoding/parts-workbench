import type {Box, Part} from './types';

const categories=['电阻','电容','电感','二极管','三极管','MOS管','芯片','MCU','连接器','开关','模块','其他'];
export function boxCategories(box:Pick<Box,'name'>):string[]{
 return categories.filter(category=>box.name.toUpperCase().includes(category.toUpperCase()));
}

export interface AllocationRow {purchase:{id:string;part_id:string};selected:boolean;slot_id:string}

// Preview only: the caller still confirms receipts through the validated stock API.
export function allocateReceiptSlots(boxes:Box[],parts:Part[],rows:AllocationRow[],boxId=''){
 const stock=new Map(parts.flatMap(p=>p.locations).map(s=>[s.slot_id,s]));
 const partById=new Map(parts.map(p=>[p.id,p]));
 const used=new Set(rows.filter(r=>r.slot_id).map(r=>r.slot_id));
 const orderedBoxes=[...boxes].sort((a,b)=>a.code.localeCompare(b.code,undefined,{numeric:true}));
 const assignments:Record<string,string>={};
 const unassigned:{id:string;reason:string}[]=[];
 for(const row of rows){
  if(!row.selected||row.slot_id)continue;
  const category=partById.get(row.purchase.part_id)?.category;
  const eligible=orderedBoxes.filter(b=>boxId?b.id===boxId:!!category&&boxCategories(b).includes(category));
  if(!eligible.length){
   unassigned.push({id:row.purchase.id,reason:boxId?'所选盒子不存在':`没有${category?'“'+category+'”类别的':'可识别类别的'}收纳盒，请手动选择`});
   continue;
  }
  const slots=eligible.flatMap(b=>[...b.slots].sort((a,b)=>a.row_no-b.row_no||a.col_no-b.col_no)).filter(s=>!used.has(s.id));
  const slot=slots.find(s=>stock.get(s.id)?.part_id===row.purchase.part_id&&stock.get(s.id)?.mode==='exact')
   ||slots.find(s=>!stock.has(s.id));
  if(!slot){unassigned.push({id:row.purchase.id,reason:'对应盒子没有可自动分配的格子，请换盒或手动选择'});continue;}
  assignments[row.purchase.id]=slot.id;
  used.add(slot.id);
 }
 return {assignments,unassigned};
}
