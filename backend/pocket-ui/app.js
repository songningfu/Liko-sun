"use strict";
const $ = id => document.getElementById(id);
const apiPrefix = document.querySelector('meta[name="pocket-api-prefix"]')?.content || "";
const kinds = {note:"随手记", idea:"灵感", task:"待办"};
let capabilities = {}, busy = false, recording = null, recorderTimer = null, recordedBlob = null, audioUrl = null, editing = null, loadSequence=0;
let requestId = sessionStorage.getItem("pocket-request-id"), savedDraft = sessionStorage.getItem("pocket-draft") || "";
$("note-text").value = savedDraft;

function message(id, text, error=false) { $(id).textContent=text; $(id).classList.toggle("error", error); }
function setBusy(value) { busy=value; ["save-button","logout","record-button"].forEach(id=>$(id).disabled=value || (id==="record-button" && !capabilities.voiceAvailable)); }
async function api(path, options={}) {
  const controller = new AbortController(), timer=setTimeout(()=>controller.abort(), 45000);
  try {
    const response = await fetch(apiPrefix + path, {...options, credentials:"same-origin", signal:controller.signal, headers:options.body instanceof FormData ? {} : {"Content-Type":"application/json"}});
    const data=response.status===204 ? null : await response.json();
    if(!response.ok) throw new Error(typeof data.detail==="string" ? data.detail : "请检查输入内容后重试。");
    return data;
  } catch(error) {
    if(error.name==="AbortError") throw new Error("等待超时。内容已保留，可以重试；同次重试不会重复保存。");
    if(error instanceof SyntaxError) throw new Error("服务返回了异常结果，请稍后重试。");
    if(error instanceof TypeError) throw new Error("暂时连接不上，内容已保留，请恢复网络后重试。");
    throw error;
  } finally { clearTimeout(timer); }
}
function rememberDraft() {
  const current=$("note-text").value;
  if(current!==savedDraft) { requestId=null; sessionStorage.removeItem("pocket-request-id");sessionStorage.removeItem("pocket-request-source"); }
  savedDraft=current; sessionStorage.setItem("pocket-draft",current);
}
$("note-text").addEventListener("input", rememberDraft);
function clearAudio() { recordedBlob=null; if(audioUrl) URL.revokeObjectURL(audioUrl); audioUrl=null; $("audio-preview").hidden=true; $("audio-preview").removeAttribute("src"); $("retry-voice").hidden=true; }

async function signedIn(data) {
  $("account").hidden=true; $("workspace").hidden=false; $("current-user").textContent=`${data.user.username} 的记录`;
  capabilities=await api("/api/pocket/status");
  $("capabilities").textContent=capabilities.analysisAvailable ? "原文先保存，再自动整理；整理结果可以核对和修改。" : "当前保存原文，可按关键词找回。自动整理尚未开启。";
  $("record-button").disabled=!capabilities.voiceAvailable || !navigator.mediaDevices?.getUserMedia || !window.MediaRecorder;
  $("recording-status").textContent=capabilities.voiceAvailable ? "单次最多 60 秒，识别后确认保存" : "语音识别尚未开启，先用文字记录";
  await loadNotes();
}
async function authenticate(register=false) {
  if(!$("auth-form").reportValidity())return;
  $("login-button").disabled=true; $("register-button").disabled=true; message("auth-status","正在连接…");
  try {
    const data=await api(`/api/auth/${register ? "register":"login"}`,{method:"POST",body:JSON.stringify({username:$("username").value.trim(),password:$("password").value})});
    $("password").value="";
    if(data.recoveryCode) {
      $("recovery-code").textContent=data.recoveryCode; $("recovery").hidden=false;
      message("auth-status","账号已创建。请先保存恢复码，再开始记录。");
    } else { $("recovery-code").textContent=""; $("recovery").hidden=true; await signedIn(data); }
  } catch(error) { message("auth-status",error.message,true); }
  finally { $("login-button").disabled=false; $("register-button").disabled=false; }
}
$("auth-form").addEventListener("submit",event=>{event.preventDefault();authenticate();});
$("register-button").addEventListener("click",()=>authenticate(true));
$("continue-recording").addEventListener("click",async()=>{
  try{const data=await api("/api/auth/me");$("recovery-code").textContent="";$("recovery").hidden=true;await signedIn(data);}
  catch(error){message("auth-status",error.message,true);}
});
$("logout").addEventListener("click",async()=>{
  if(busy)return;
  try { await api("/api/auth/logout",{method:"POST"}); sessionStorage.removeItem("pocket-draft");sessionStorage.removeItem("pocket-request-id");sessionStorage.removeItem("pocket-request-source");clearAudio();location.reload(); }
  catch(error){message("save-status",error.message,true);}
});
$("save-button").addEventListener("click",async()=>{
  const text=$("note-text").value.trim();
  if(!text){message("save-status","先写下一条想记住的内容。",true);$("note-text").focus();return;}
  rememberDraft(); requestId ||= crypto.randomUUID(); sessionStorage.setItem("pocket-request-id",requestId);
  const source=sessionStorage.getItem("pocket-request-source") || (recordedBlob ? "browser_voice":"text");
  sessionStorage.setItem("pocket-request-source",source);
  setBusy(true);message("save-status","正在保存，请稍候…");
  try {
    const note=await api("/api/pocket/records",{method:"POST",body:JSON.stringify({requestId,text,source})});
    $("note-text").value=""; savedDraft=""; requestId=null;sessionStorage.removeItem("pocket-draft");sessionStorage.removeItem("pocket-request-id");sessionStorage.removeItem("pocket-request-source");clearAudio();
    message("save-status",note.analysisState==="failed" ? "原文已保存，自动整理暂未完成。" : "已保存，可以在下面找回。");await loadNotes();
  }catch(error){message("save-status",error.message,true);}finally{setBusy(false);}
});

function node(tag, text, className) { const element=document.createElement(tag);if(text!==undefined)element.textContent=text;if(className)element.className=className;return element; }
function renderNote(note) {
  const article=node("article",undefined,"note"), heading=node("div",undefined,"note-header"), edit=node("button","修改","text-button");
  heading.append(node("h3",note.title,"note-title"),edit);edit.addEventListener("click",()=>openEditor(note));
  const state=note.analysisState==="ready" ? "已自动整理，请核对" : note.analysisState==="failed" ? "原文已保存 · 整理失败" : "原文记录";
  article.append(heading,node("p",`${kinds[note.kind]} · ${new Date(note.createdAt).toLocaleString("zh-CN")} · ${state}`,"note-meta"),node("p",note.summary || note.text,"note-body"));
  if(note.tasks.length){const ul=node("ul");note.tasks.forEach(task=>ul.append(node("li",task.text+(task.dueText ? ` · ${task.dueText}`:""))));article.append(ul);}
  const detail=node("details");detail.append(node("summary","查看正文与初始原文"),node("p",`当前正文：\n${note.text}`),node("p",`初始${note.source==="browser_voice" ? "转写":"原文"}：\n${note.originalText}`));article.append(detail);
  if(capabilities.analysisAvailable && note.analysisState!=="ready"){
    const retry=node("button","自动整理","text-button");retry.addEventListener("click",async()=>{retry.disabled=true;retry.textContent="正在整理…";try{await api(`/api/pocket/records/${note.id}/analyze`,{method:"POST"});await loadNotes();}catch(error){message("list-status",error.message,true);}finally{retry.disabled=false;retry.textContent="自动整理";}});article.append(retry);
  }
  return article;
}
async function loadNotes() {
  const sequence=++loadSequence;
  message("list-status","正在找回记录…");
  try {
    const params=new URLSearchParams({q:$("query").value.trim()});if($("kind-filter").value)params.set("kind",$("kind-filter").value);
    const notes=await api("/api/pocket/records?"+params);if(sequence!==loadSequence)return;$("notes").replaceChildren(...notes.map(renderNote));
    message("list-status",`按原文关键词查找 · 当前显示 ${notes.length} 条，最多 50 条`);
    if(!notes.length)$("notes").append(node("div",$("query").value.trim() ? "还没有匹配的记录，试试更短的关键词。":"还没有记录。先在上面记下第一条。","empty"));
  }catch(error){message("list-status",error.message,true);}
}
$("search-form").addEventListener("submit",event=>{event.preventDefault();loadNotes();});
function openEditor(note){editing=note;$("edit-text").value=note.text;$("edit-kind").value=note.kind;message("edit-status","");$("edit-dialog").showModal();}
$("close-editor").addEventListener("click",()=>$("edit-dialog").close());
$("edit-form").addEventListener("submit",async event=>{
  event.preventDefault();$("edit-save").disabled=true;$("delete-button").disabled=true;message("edit-status","正在保存…");
  try{await api(`/api/pocket/records/${editing.id}`,{method:"PATCH",body:JSON.stringify({text:$("edit-text").value,kind:$("edit-kind").value,revision:editing.revision})});$("edit-dialog").close();await loadNotes();}
  catch(error){message("edit-status",error.message,true);}finally{$("edit-save").disabled=false;$("delete-button").disabled=false;}
});
$("delete-button").addEventListener("click",async()=>{
  if(!confirm("删除这条记录？正文、初始原文和整理结果都会删除。"))return;
  $("delete-button").disabled=true;$("edit-save").disabled=true;
  try{await api(`/api/pocket/records/${editing.id}`,{method:"DELETE"});$("edit-dialog").close();await loadNotes();}
  catch(error){message("edit-status",error.message,true);}finally{$("delete-button").disabled=false;$("edit-save").disabled=false;}
});

async function transcribeRecording(){
  if(!recordedBlob || busy)return;setBusy(true);$("retry-voice").hidden=true;message("recording-status","正在识别，完成后请核对内容…");
  const data=new FormData();data.append("file",recordedBlob,"recording");
  try{const result=await api("/api/pocket/transcribe",{method:"POST",body:data});$("note-text").value=result.text;rememberDraft();message("recording-status","识别完成，请核对文字后保存。");}
  catch(error){message("recording-status",error.message,true);$("retry-voice").hidden=false;}
  finally{setBusy(false);}
}
$("retry-voice").addEventListener("click",transcribeRecording);
$("record-button").addEventListener("click",async()=>{
  if(recording){recording.stop();return;}
  if(busy)return;
  try{
    const stream=await navigator.mediaDevices.getUserMedia({audio:true});
    let chunks=[];
    try{const type=["audio/webm","audio/mp4","audio/ogg"].find(value=>MediaRecorder.isTypeSupported(value));recording=new MediaRecorder(stream,type ? {mimeType:type}:{});}
    catch(error){stream.getTracks().forEach(track=>track.stop());throw error;}
    const active=recording;
    active.ondataavailable=event=>{if(event.data.size)chunks.push(event.data);};
    active.onstop=()=>{
      clearTimeout(recorderTimer);stream.getTracks().forEach(track=>track.stop());clearAudio();recordedBlob=new Blob(chunks,{type:active.mimeType});audioUrl=URL.createObjectURL(recordedBlob);$("audio-preview").src=audioUrl;$("audio-preview").hidden=false;
      recording=null;$("record-button").textContent="重新录音";$("record-button").classList.remove("is-recording");setBusy(false);transcribeRecording();
    };
    active.start();setBusy(true);$("record-button").disabled=false;$("record-button").textContent="结束录音";$("record-button").classList.add("is-recording");message("recording-status","正在录音，点击结束；60 秒后自动停止。");
    recorderTimer=setTimeout(()=>{if(active.state==="recording")active.stop();},60000);
  }catch(error){message("recording-status","无法使用麦克风，请检查浏览器权限，或先用文字记录。",true);}
});
window.addEventListener("beforeunload",event=>{if(busy || recording){event.preventDefault();event.returnValue="";}});
(async()=>{try{const data=await api("/api/auth/me");if(data.authenticated)await signedIn(data);}catch(error){message("auth-status",error.message,true);}})();
