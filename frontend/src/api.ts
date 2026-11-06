export async function api<T>(path:string, method='GET', body?:unknown):Promise<T>{
 const form=body instanceof FormData;
 const res=await fetch('/api'+path,{method,headers:form?{}:body?{'Content-Type':'application/json'}:{},body:body===undefined?undefined:form?body:JSON.stringify(body)});
 if(!res.ok){if(res.status===401&&!path.startsWith('/auth/'))window.dispatchEvent(new Event('session-expired'));const error=await res.json().catch(()=>({detail:'服务暂时不可用，请检查是否已启动'}));const detail=error.detail;throw new Error(typeof detail==='string'?detail:Array.isArray(detail)?detail.map((x:{msg:string})=>x.msg).join('；'):'操作失败');}
 return res.json();
}
