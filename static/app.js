const navToggle=document.querySelector('#nav-toggle'),sidebar=document.querySelector('#sidebar'),navBackdrop=document.querySelector('#nav-backdrop'),navMedia=matchMedia('(max-width:700px)'),headerUser=navToggle?.parentElement.querySelector('strong');
function setNav(open,returnFocus=false){if(!sidebar)return;const active=navMedia.matches&&open;headerUser.style.cssText=navMedia.matches?'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap':'';sidebar.classList.toggle('is-open',active);sidebar.inert=navMedia.matches&&!active;navBackdrop.hidden=!active;navToggle.setAttribute('aria-expanded',String(active));navToggle.setAttribute('aria-label',active?'Close navigation':'Open navigation');if(active)sidebar.focus();else if(returnFocus)navToggle.focus()}
navToggle?.addEventListener('click',()=>setNav(!sidebar.classList.contains('is-open'),true));navBackdrop?.addEventListener('click',()=>setNav(false,true));navMedia.addEventListener('change',()=>setNav(false));setNav(false);addEventListener('keydown',event=>{if(event.key==='Escape'&&sidebar?.classList.contains('is-open'))setNav(false,true);if(event.key!=='Tab'||!sidebar?.classList.contains('is-open'))return;const items=sidebar.querySelectorAll('a,button:not([disabled])'),first=items[0],last=items[items.length-1];if(document.activeElement===sidebar){event.preventDefault();(event.shiftKey?last:first).focus()}else if((event.shiftKey&&document.activeElement===first)||(!event.shiftKey&&document.activeElement===last)){event.preventDefault();(event.shiftKey?last:first).focus()}});
document.querySelectorAll('form').forEach(form=>form.addEventListener('submit',event=>{const button=event.submitter;if(button?.classList.contains('danger')&&!confirm('Continue?'))event.preventDefault()}));

const priceLookup=document.querySelector('#lookup-price');
priceLookup?.addEventListener('click',async()=>{const form=priceLookup.closest('form'),asset=form.elements.asset_id.value,status=document.querySelector('#lookup-status');priceLookup.disabled=true;status.textContent='Loading…';try{const body=new URLSearchParams({csrf_token:form.elements.csrf_token.value});const response=await fetch('/api/assets/'+encodeURIComponent(asset)+'/price',{method:'POST',body});if(!response.ok)throw new Error('Price unavailable');const quote=await response.json();if(form.elements.asset_id.value===asset){form.elements.price.value=quote.price;status.textContent=quote.source+' · '+quote.timestamp;}}catch(error){status.textContent='Price unavailable. Enter a price manually.';}finally{priceLookup.disabled=false;}});
document.querySelectorAll('form[action="/refresh"]').forEach(form=>form.addEventListener('submit',()=>{form.querySelector('button').disabled=true;}));
addEventListener('pageshow',()=>{document.querySelectorAll('form[action="/refresh"] button').forEach(button=>button.disabled=false);});

const aiTestDialog=document.querySelector('#ai-test-dialog');
document.querySelectorAll('form[data-ai-test]').forEach(form=>form.addEventListener('submit',async event=>{
  event.preventDefault();
  const button=form.querySelector('button');
  if(button.disabled)return;
  button.disabled=true;
  form.setAttribute('aria-busy','true');
  let message=aiTestDialog.dataset.failure;
  try{
    const response=await fetch(form.action,{method:'POST',body:new FormData(form),headers:{Accept:'application/json'}});
    const result=await response.json();
    if(typeof result.message==='string')message=result.message;
  }catch(error){/* Show the localized fallback without leaving Settings. */}
  finally{button.disabled=false;form.removeAttribute('aria-busy');}
  document.querySelector('#ai-test-message').textContent=message;
  if(!aiTestDialog.open)aiTestDialog.showModal();
}));
