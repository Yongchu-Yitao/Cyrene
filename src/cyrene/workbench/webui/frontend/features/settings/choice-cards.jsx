export function ChoiceCards({label,value,onChange,options,compact=false,descriptionId}) {
  const group=React.useId();
  return <div className={compact?"inline-choice":"choice-container"}>
    <div className={compact?"inline-options":"map-options"} role="radiogroup" aria-label={label} style={{'--choice-count':options.length}}>
      {options.map(({id,title,detail,disabled})=>
        <label key={id} className={compact?"inline-option":"map-option"} data-selected={value===id} data-disabled={!!disabled}>
          <input type="radio" name={group} value={id} checked={value===id} disabled={disabled} aria-label={typeof title==='string'?title:undefined} aria-describedby={detail?group+'-'+id:disabled?descriptionId:undefined} onChange={()=>onChange(id)}/>
          <span><strong>{title}</strong>{detail&&<small id={group+'-'+id}>{detail}</small>}</span>
        </label>
      )}
    </div>
  </div>
}
