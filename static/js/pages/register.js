function togglePw(inputId, showId, hideId) {
  var inp = document.getElementById(inputId);
  if (!inp) return;
  var isHidden = inp.type === 'password';
  inp.type = isHidden ? 'text' : 'password';
  var show = document.getElementById(showId);
  var hide = document.getElementById(hideId);
  if (show) show.style.display = isHidden ? 'none' : '';
  if (hide) hide.style.display = isHidden ? '' : 'none';
}

// V70 CSP: bind .pw-toggle buttons via data-* instead of inline onclick.
document.querySelectorAll('.pw-toggle[data-pw-target]').forEach(function(btn){
  btn.addEventListener('click', function(){
    togglePw(btn.dataset.pwTarget, btn.dataset.showId, btn.dataset.hideId);
  });
});

// Password strength
var pwInput = document.getElementById('reg-password');
pwInput.addEventListener('input', function() {
  var val = this.value;
  var score = 0;
  if (val.length >= 6) score++;
  if (val.length >= 10) score++;
  if (/[A-Z]/.test(val)) score++;
  if (/[0-9]/.test(val)) score++;
  if (/[^A-Za-z0-9]/.test(val)) score++;
  var fill = document.getElementById('pw-strength-fill');
  var label = document.getElementById('pw-strength-label');
  var colors = ['#ef4444','#f97316','#eab308','#22c55e','#16a34a'];
  var labels = ['\u0636\u0639\u064a\u0641\u0629 \u062c\u062f\u0627\u064b','\u0636\u0639\u064a\u0641\u0629','\u0645\u062a\u0648\u0633\u0637\u0629','\u062c\u064a\u062f\u0629','\u0642\u0648\u064a\u0629 \u062c\u062f\u0627\u064b'];
  if (val.length === 0) { fill.style.width='0'; label.textContent=''; return; }
  fill.style.width = ((score/5)*100)+'%';
  fill.style.background = colors[score-1] || colors[0];
  label.textContent = labels[score-1] || labels[0];
  label.style.color = colors[score-1] || colors[0];
});

// Confirm match check
document.getElementById('reg-confirm').addEventListener('input', function() {
  var err = document.getElementById('err-confirm');
  var msgEl = document.getElementById('reg-form');
  var msg = msgEl ? msgEl.dataset.errMismatch || 'Passwords do not match' : 'Passwords do not match';
  if (this.value && this.value !== pwInput.value) {
    err.textContent = msg;
  } else {
    err.textContent = '';
  }
});

// Form validation
document.getElementById('reg-form').addEventListener('submit', function(e) {
  var ok = true;
  var clearErr = function(id) { document.getElementById(id).textContent = ''; };
  ['err-name','err-reg-email','err-reg-password','err-confirm'].forEach(clearErr);

  var form = document.getElementById('reg-form');
  var name = document.getElementById('reg-name');
  var email = document.getElementById('reg-email');
  var pass = document.getElementById('reg-password');
  var conf = document.getElementById('reg-confirm');

  if (!name.value.trim()) {
    document.getElementById('err-name').textContent = form.dataset.errName || 'Name is required';
    name.classList.add('input-err'); ok = false;
  } else name.classList.remove('input-err');

  if (!email.value.trim() || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value)) {
    document.getElementById('err-reg-email').textContent = form.dataset.errEmail || 'Enter a valid email';
    email.classList.add('input-err'); ok = false;
  } else email.classList.remove('input-err');

  if (pass.value.length < 8) {
    document.getElementById('err-reg-password').textContent = form.dataset.errPassword || 'Minimum 8 characters';
    pass.classList.add('input-err'); ok = false;
  } else pass.classList.remove('input-err');

  if (conf.value !== pass.value) {
    document.getElementById('err-confirm').textContent = form.dataset.errMismatch || 'Passwords do not match';
    conf.classList.add('input-err'); ok = false;
  } else conf.classList.remove('input-err');

  if (!ok) e.preventDefault();
});
