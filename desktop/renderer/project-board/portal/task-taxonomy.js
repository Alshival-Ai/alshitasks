/* Bundled unchanged in the desktop app. No portal dependency. */
(() => {
  const icons = ['task','bug','story','improvement','research','design','support','maintenance'];
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  window.AlshivalTaskTaxonomy = function(root, request) {
    let state, stopped = false, busy = false;
    const error = element('p', '', 'taxonomy-error'); error.setAttribute('role','alert');
    const content = element('div'); root.classList.add('taxonomy-settings'); root.replaceChildren(error, content);
    function button(text, action) {
      const node = element('button', text, 'button btn-frost'); node.type = 'button';
      node.addEventListener('click', action); return node;
    }
    async function write(args) {
      if (busy) return;
      busy = true; error.textContent = '';
      const controls=Array.from(content.querySelectorAll('button')).filter(n=>!n.disabled);
      controls.forEach(n => n.disabled = true);
      try { state = await request('POST', {...args, revision: state.revision, idempotency_key: crypto.randomUUID()}); render(); }
      catch (e) { error.textContent = e.message; }
      finally { busy = false; controls.forEach(n => n.disabled = false); }
    }
    function iconSelect(value) {
      const select = element('select'); select.setAttribute('aria-label','Type icon');
      icons.forEach(icon => { const option = element('option',icon[0].toUpperCase()+icon.slice(1)); option.value=icon; select.append(option); });
      select.value = value || 'task'; return select;
    }
    function render() {
      if (stopped) return;
      content.replaceChildren();
      if (state.review?.length) {
        const review = element('section'); review.append(element('h3','Sync changes need review'), element('p','Your local settings are preserved. Review the portal settings before choosing which changes to keep.'));
        const details = element('ul');
        for(const [key,title] of [['types','Portal types'],['labels','Portal labels']]) details.append(element('li',title+': '+(state.remote?.[key] || []).map(row=>row.name+(row.archived?' (archived)':'')).join(', ')));
        review.append(details);
        for (const choice of ['local','remote']) review.append(button(choice === 'local' ? 'Retry my changes' : 'Use portal settings', async () => {
          try { state = await request('REVIEW', {choice, revision: state.remote?.revision}); render(); error.textContent=''; }
          catch(e) { error.textContent=e.message; }
        }));
        content.append(review);
      }
      for (const kind of ['type','label']) {
        const section = element('section'); section.append(element('h3',kind === 'type' ? 'Task types' : 'Labels'));
        const rows = state[kind === 'type' ? 'types' : 'labels'];
        for (const row of rows) {
          const line = element('div', undefined, 'taxonomy-row' + (row.archived ? ' taxonomy-archived' : ''));
          const name = element('input'); name.value=row.name; name.maxLength=60; name.setAttribute('aria-label',kind === 'type' ? 'Type name' : 'Label name'); line.append(name);
          const icon = kind === 'type' ? iconSelect(row.icon) : null;
          if (icon) line.append(icon);
          if (row.archived) { line.append(element('span','Archived')); name.disabled = true; if(icon) icon.disabled=true; }
          else if (state.can_manage !== false) {
            line.append(button('Save', () => write({kind,operation:'update',id:row.id,name:name.value,...(icon?{icon:icon.value}:{})})));
            if (kind === 'type') {
              if (row.is_default) line.append(element('span','Default'));
              else line.append(button('Set default', () => write({kind,operation:'default',id:row.id})));
              const active = rows.filter(r => !r.archived), index = active.findIndex(r => r.id === row.id);
              for (const [caption, offset] of [['Move up',-1],['Move down',1]]) {
                const move = button(caption, () => {const order=active.map(r=>r.id); [order[index],order[index+offset]]=[order[index+offset],order[index]]; write({kind,operation:'reorder',order});});
                move.disabled=index+offset<0 || index+offset>=active.length; line.append(move);
              }
            }
            if (!row.is_default) line.append(button('Archive', () => write({kind,operation:'archive',id:row.id})));
          } else {name.disabled=true; if(icon) icon.disabled=true;}
          section.append(line);
        }
        if (state.can_manage !== false) {
          const form=element('form',undefined,'taxonomy-create'), name=element('input'); name.required=true; name.maxLength=60; name.placeholder=kind==='type'?'New task type':'New label'; name.setAttribute('aria-label',name.placeholder); form.append(name);
          const icon=kind==='type'?iconSelect('task'):null;if(icon)form.append(icon);
          const add=element('button',kind==='type'?'Add type':'Add label','button btn matrix-btn');add.type='submit';form.append(add);
          form.addEventListener('submit',e=>{e.preventDefault();write({kind,operation:'create',name:name.value,...(icon?{icon:icon.value}:{})});});section.append(form);
        }
        content.append(section);
      }
    }
    const reload=button('Reload settings',()=>request('GET').then(value=>{state=value;error.textContent='';render();}).catch(e=>error.textContent=e.message));
    root.append(reload);
    request('GET').then(value=>{state=value;render();}).catch(e=>error.textContent=e.message);
    return () => {stopped=true;};
  };
  document.addEventListener('input', event => {
    if (!event.target.matches('[data-taxonomy-label-search]')) return;
    const query=event.target.value.toLowerCase(), select=event.target.parentElement.querySelector('select');
    if(select)Array.from(select.options).forEach(option=>option.hidden=!option.text.toLowerCase().includes(query));
    event.target.parentElement.querySelectorAll('.taxonomy-label-options label').forEach(label=>label.hidden=!label.textContent.toLowerCase().includes(query));
  });
  function mount() {
    document.querySelectorAll('[data-task-taxonomy-url]').forEach(root => {
      if(root.dataset.taxonomyMounted)return;root.dataset.taxonomyMounted='true';
      window.AlshivalTaskTaxonomy(root,async(method,body)=>{
        const response=await fetch(root.dataset.taskTaxonomyUrl,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':root.dataset.csrf},...(body?{body:JSON.stringify(body)}:{})});
        const result=await response.json();if(!response.ok)throw new Error(result.error || 'Unable to save workspace settings');return result;
      });
    });
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount);else mount();
  document.addEventListener('alshival:page',mount);
})();
