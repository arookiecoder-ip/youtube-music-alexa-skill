(() => {
'use strict';
const selectors='.transport button, #pp-btn, [data-action="next"], [data-action="previous"], #mobile-np-play, #mobile-np-next, #mobile-np-previous, .progress-track, #volume, #mobile-np-volume, .queue-item, .np-queue-item';
let locked=false,lastToast=0;
window.updateAlexaControlState=np=>{
 locked=np?.playback_output==='alexa' && np.remote_control_available===false;
 document.body.classList.toggle('alexa-controls-unavailable',locked);
 if(typeof np?.amazon_connected==='boolean')window.setAmazonConnection?.(np.amazon_connected);
 document.querySelectorAll(selectors).forEach(el=>{if(locked)el.setAttribute('aria-disabled','true');else el.removeAttribute('aria-disabled');});
};
function block(e){if(e.type==='keydown'&&['Tab','Escape'].includes(e.key))return;if(!locked||!e.target.closest(selectors))return;if(e.type==='pointerdown'&&e.target.closest('.queue-item,.np-queue-item'))return;e.preventDefault();e.stopImmediatePropagation();if(Date.now()-lastToast>1500){lastToast=Date.now();window.toast?.('Sign in to Amazon to control Alexa, or choose another playback device.','error');}}
['click','pointerdown','keydown'].forEach(type=>document.addEventListener(type,block,true));
})();
