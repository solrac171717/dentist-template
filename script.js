// Header scroll shadow
const header = document.getElementById('header');
window.addEventListener('scroll', () => {
  header.classList.toggle('scrolled', window.scrollY > 20);
}, { passive: true });

// Floating WA button (desktop only — mobile uses sticky bar)
const waFloat = document.getElementById('wa-float');
window.addEventListener('scroll', () => {
  waFloat.classList.toggle('visible', window.scrollY > 300);
}, { passive: true });

// Mobile sticky CTA: ocultar cuando el botón hero está visible
const mobCta = document.getElementById('mob-cta');
const heroCta = document.querySelector('.btn-wa--hero');

if (heroCta && mobCta) {
  const heroObserver = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      mobCta.style.transform = entry.isIntersecting ? 'translateY(100%)' : 'translateY(0)';
      mobCta.style.opacity  = entry.isIntersecting ? '0' : '1';
    });
  }, { threshold: 0.5 });
  heroObserver.observe(heroCta);
}

// FAQ accordion
document.querySelectorAll('.faq__question').forEach(btn => {
  btn.addEventListener('click', () => {
    const item = btn.closest('.faq__item');
    const isOpen = item.classList.contains('open');
    document.querySelectorAll('.faq__item.open').forEach(o => {
      o.classList.remove('open');
      o.querySelector('.faq__question').setAttribute('aria-expanded', 'false');
    });
    if (!isOpen) {
      item.classList.add('open');
      btn.setAttribute('aria-expanded', 'true');
    }
  });
});

// Scroll reveal
const revealObserver = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.add('visible');
      revealObserver.unobserve(entry.target);
    }
  });
}, { threshold: 0.1 });

['.stat__card', '.step', '.team__card', '.testimonial__card', '.faq__item'].forEach(sel => {
  document.querySelectorAll(sel).forEach((el, i) => {
    el.classList.add('reveal');
    el.style.transitionDelay = `${i * 0.06}s`;
    revealObserver.observe(el);
  });
});

// Mob CTA transition setup
if (mobCta) {
  mobCta.style.transition = 'transform 0.3s ease, opacity 0.3s ease';
}
