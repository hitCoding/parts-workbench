import type {Part} from './types';
export function cleanProductName(name:string){return name.split(/[;；]/,1)[0]!.replace(/编带|托盘|系列|袋装/g,'').replace(/\s+/g,' ').trim();}
export function productName(p?:Part){
 if(!p)return '空格';
 const lines=p.notes.split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
 const source=lines.findIndex(line=>/^来自立创订单\s+[A-Z0-9_-]+$/.test(line));
 return cleanProductName(p.product_name||(source>0?lines[source-1]! : p.name))||p.value||p.category;
}

export function partLabel(p:Part){
 const description=productName(p);
 // Some imported chips are categorized as “其他”; include models for every
 // non-passive part so they remain distinguishable in BOM matching.
 return p.name&&!['电阻','电容','电感'].includes(p.category)&&!description.toLocaleLowerCase().includes(p.name.toLocaleLowerCase())
  ? `${p.name} · ${description}` : description;
}
