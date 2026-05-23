/* ==========================================================================
   THE PARTY LEDGER — calendar.js
   FullCalendar wiring, modal flow, voting, multi-day + timed events.
   ========================================================================== */

(function () {
    'use strict';

    const STATUS_LABELS = {
        session:   'Session',
        busy:      'Busy',
        tentative: 'Tentative',
        available: 'Available',
    };

    const STATUS_ICON = {
        session:   '⚔',
        busy:      '✖',
        tentative: '❔',
        available: '✔',
    };

    const CONFLICTING_STATUSES = new Set(['busy', 'tentative']);

    // ---------- State ----------
    let calendar = null;
    let cachedEvents = [];
    let sessionDates = new Set();   // every individual date covered by a session
    let lastNameUsed = '';
    let sessionToken = '';
    try { lastNameUsed = localStorage.getItem('partyLedger.name') || ''; } catch (_) {}
    try { sessionToken = localStorage.getItem('partyLedger.token') || ''; } catch (_) {}

    // ---------- DOM refs ----------
    const calendarEl   = document.getElementById('calendar');
    const dayModal     = document.getElementById('day-modal');
    const eventModal   = document.getElementById('event-modal');
    const form         = document.getElementById('event-form');
    const errorBox     = document.getElementById('modal-error');
    const modalTitleEl = document.getElementById('event-modal-title');
    const submitBtn    = document.getElementById('event-submit-btn');

    const allDayCheckbox = document.getElementById('event-all-day');
    const startDateInput = document.getElementById('event-start-date');
    const endDateInput   = document.getElementById('event-end-date');
    const startTimeInput = document.getElementById('event-start-time');
    const endTimeInput   = document.getElementById('event-end-time');
    const datetimeHint   = document.getElementById('datetime-hint');

    // ---------- Date / time helpers ----------
    function toIsoDate(d) {
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        return `${y}-${m}-${day}`;
    }
    function addDaysIso(iso, days) {
        const d = new Date(iso + 'T00:00:00');
        d.setDate(d.getDate() + days);
        return toIsoDate(d);
    }
    function isoDatesInRange(startIso, endIso) {
        const out = [];
        if (!startIso || !endIso || endIso < startIso) return out;
        const cur = new Date(startIso + 'T00:00:00');
        const end = new Date(endIso + 'T00:00:00');
        while (cur <= end) {
            out.push(toIsoDate(cur));
            cur.setDate(cur.getDate() + 1);
        }
        return out;
    }
    function formatHumanDate(iso) {
        const d = new Date(iso + 'T00:00:00');
        if (isNaN(d.getTime())) return iso;
        return d.toLocaleDateString(undefined, {
            weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
        });
    }
    function formatShortDate(iso) {
        const d = new Date(iso + 'T00:00:00');
        if (isNaN(d.getTime())) return iso;
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    }
    function formatTime(t24) {
        if (!t24) return '';
        const [h, m] = t24.split(':').map(Number);
        if (isNaN(h)) return t24;
        const ampm = h >= 12 ? 'pm' : 'am';
        const h12 = h % 12 || 12;
        return `${h12}:${String(m).padStart(2, '0')}${ampm}`;
    }
    function formatEventTimeSpan(e) {
        if (e.all_day) {
            if (e.start_date === e.end_date) return 'All day';
            return `All day · ${formatShortDate(e.start_date)} – ${formatShortDate(e.end_date)}`;
        }
        const st = formatTime(e.start_time);
        const et = formatTime(e.end_time);
        if (e.start_date === e.end_date) return `${st} – ${et}`;
        return `${formatShortDate(e.start_date)} ${st} – ${formatShortDate(e.end_date)} ${et}`;
    }

    function eventCoversDay(e, dateIso) {
        return e.start_date <= dateIso && e.end_date >= dateIso;
    }

    function datetimeToMinutes(dateIso, timeStr, allDay) {
        // Convert a date+time to minutes since epoch for comparison.
        // For all-day events, use midnight.
        const d = new Date(dateIso + 'T00:00:00Z');
        const minutes = Math.floor(d.getTime() / 60000);
        if (allDay) return { start: minutes, end: minutes + 24 * 60 };
        const [h, m] = (timeStr || '00:00').split(':').map(Number);
        const offset = h * 60 + m;
        return { start: minutes + offset, end: minutes + offset };
    }

    function rangeOverlapsAnySession(startIso, endIso, isAllDay, startTime, endTime) {
        // Check if the given event (by date/time) overlaps with any session event.
        for (const sess of cachedEvents) {
            if (sess.status !== 'session') continue;
            
            const eventStart = datetimeToMinutes(startIso, startTime, isAllDay);
            const eventEnd = datetimeToMinutes(endIso, endTime, isAllDay);
            
            const sessStart = datetimeToMinutes(sess.start_date, sess.start_time, sess.all_day);
            const sessEnd = datetimeToMinutes(sess.end_date, sess.end_time, sess.all_day);
            
            // Check for overlap: [a, b) overlaps [c, d) iff a < d and c < b
            if (eventStart.start < sessEnd.end && sessStart.start < eventEnd.end) {
                return true;
            }
        }
        return false;
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function showError(msg) {
        errorBox.textContent = msg;
        errorBox.hidden = false;
    }
    function clearError() {
        errorBox.textContent = '';
        errorBox.hidden = true;
    }

    function setSubmitting(isSubmitting, editing) {
        submitBtn.disabled = isSubmitting;
        if (isSubmitting) {
            submitBtn.textContent = editing ? 'Amending…' : 'Inscribing…';
        } else {
            submitBtn.textContent = editing ? 'Amend' : 'Inscribe';
        }
    }

    function ensureName(promptText) {
        if (lastNameUsed) return lastNameUsed;
        const entered = prompt(promptText || 'Thy name:');
        if (!entered) return null;
        const trimmed = entered.trim();
        if (!trimmed) return null;
        lastNameUsed = trimmed;
        try { localStorage.setItem('partyLedger.name', trimmed); } catch (_) {}
        return trimmed;
    }

    // ---------- API ----------
    async function fetchEvents() {
        const url = lastNameUsed
            ? `/api/events?voter=${encodeURIComponent(lastNameUsed)}`
            : '/api/events';
        const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
        if (!res.ok) throw new Error('Failed to fetch events');
        return res.json();
    }
    async function ensureSessionToken() {
        if (!sessionToken) {
            try {
                const res = await fetch('/api/session-token');
                if (res.ok) {
                    const data = await res.json();
                    sessionToken = data.token;
                    localStorage.setItem('partyLedger.token', sessionToken);
                }
            } catch (e) {
                console.error('Failed to get session token:', e);
            }
        }
        return sessionToken;
    }
    async function createEvent(payload) {
        const res = await fetch('/api/events', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...payload, creator_token: sessionToken }),
        });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.error || 'The scribe falters. Try again.');
        }
        return res.json();
    }
    async function patchEvent(id, payload) {
        const res = await fetch(`/api/events/${encodeURIComponent(id)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...payload, creator_token: sessionToken }),
        });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.error || 'Could not amend the entry.');
        }
        return res.json();
    }
    async function deleteEventApi(id) {
        const res = await fetch(`/api/events/${encodeURIComponent(id)}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ creator_token: sessionToken }),
        });
        if (!res.ok && res.status !== 204) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.error || 'Could not delete entry.');
        }
    }
    async function castVote(eventId, voterName, value) {
        const res = await fetch(`/api/events/${encodeURIComponent(eventId)}/vote`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ voter_name: voterName, value }),
        });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.error || 'Vote failed.');
        }
        return res.json();
    }

    // ---------- Calendar rendering ----------
    function eventToFcSource(e) {
        const base = {
            id: String(e.id),
            title: `${e.name}: ${e.title}`,
            classNames: ['evt-' + e.status],
            extendedProps: { raw: e },
        };
        if (e.all_day) {
            return {
                ...base,
                start: e.start_date,
                // FullCalendar `end` for all-day is EXCLUSIVE — add a day.
                end: addDaysIso(e.end_date, 1),
                allDay: true,
            };
        }
        return {
            ...base,
            start: `${e.start_date}T${e.start_time || '00:00'}`,
            end: `${e.end_date}T${e.end_time || '23:59'}`,
            allDay: false,
        };
    }

    function recomputeSessionDates() {
        sessionDates = new Set();
        cachedEvents.forEach(e => {
            if (e.status !== 'session') return;
            for (const iso of isoDatesInRange(e.start_date, e.end_date)) {
                sessionDates.add(iso);
            }
        });
    }

    async function refreshCalendar() {
        cachedEvents = await fetchEvents();
        recomputeSessionDates();
        if (!calendar) return;
        calendar.removeAllEvents();
        cachedEvents.forEach(ev => calendar.addEvent(eventToFcSource(ev)));
    }

    // ---------- Day modal ----------
    function renderVoteControls(event) {
        const myVote = event.votes && event.votes.my_vote;
        const net = (event.votes && event.votes.net) || 0;
        const up = (event.votes && event.votes.up) || 0;
        const down = (event.votes && event.votes.down) || 0;
        const netClass = net > 0 ? 'pos' : net < 0 ? 'neg' : 'zero';
        const netDisplay = net > 0 ? `+${net}` : `${net}`;
        return `
            <div class="vote-controls" data-event-id="${escapeHtml(event.id)}">
                <button type="button" class="vote-btn vote-up ${myVote === 1 ? 'voted' : ''}"
                        data-vote="1" aria-label="Upvote" title="Honest reason">
                    ▲
                </button>
                <span class="vote-tally vote-tally-${netClass}" title="${up} upvotes, ${down} downvotes">
                    ${netDisplay}
                </span>
                <button type="button" class="vote-btn vote-down ${myVote === -1 ? 'voted' : ''}"
                        data-vote="-1" aria-label="Downvote" title="Weak excuse">
                    ▼
                </button>
            </div>
        `;
    }

    function renderEventRow(e, hasSessionOnDay) {
        const status = STATUS_LABELS[e.status] || e.status;
        const icon = STATUS_ICON[e.status] || '';
        
        // Check if THIS specific event overlaps with a session (not just if a session exists on the day)
        const eventOverlapsSession = CONFLICTING_STATUSES.has(e.status) && 
            rangeOverlapsAnySession(e.start_date, e.end_date, e.all_day, e.start_time, e.end_time);
        const isConflict = eventOverlapsSession;

        // ---- Time gutter (the left column) ----
        const gutterMain = e.all_day
            ? 'All day'
            : formatTime(e.start_time);
        const timeGutter = `<span class="time-gutter-main">${escapeHtml(gutterMain)}</span>`;

        // ---- Span subtitle (only when there's something extra to show) ----
        let whenSubtitle = '';
        if (e.all_day && e.start_date !== e.end_date) {
            const days = isoDatesInRange(e.start_date, e.end_date).length;
            whenSubtitle = `<div class="day-event-when">${escapeHtml(formatShortDate(e.start_date))} – ${escapeHtml(formatShortDate(e.end_date))} · ${days} days</div>`;
        } else if (!e.all_day) {
            if (e.start_date === e.end_date) {
                whenSubtitle = `<div class="day-event-when">until ${escapeHtml(formatTime(e.end_time))}</div>`;
            } else {
                whenSubtitle = `<div class="day-event-when">${escapeHtml(formatShortDate(e.start_date))} ${escapeHtml(formatTime(e.start_time))} – ${escapeHtml(formatShortDate(e.end_date))} ${escapeHtml(formatTime(e.end_time))}</div>`;
            }
        }

        const desc = e.description
            ? `<p class="day-event-desc">${escapeHtml(e.description)}</p>`
            : '';

        let reasoningBlock = '';
        if (isConflict) {
            if (e.reasoning) {
                reasoningBlock = `<blockquote class="day-event-reasoning">${escapeHtml(e.reasoning)}</blockquote>`;
            } else {
                reasoningBlock = `
                    <div class="day-event-reasoning day-event-reasoning-missing">
                        <em>No reasoning given.</em>
                        <button type="button" class="link-btn" data-edit-id="${escapeHtml(e.id)}">Add reasoning</button>
                    </div>
                `;
            }
        }

        const voteControls = isConflict && e.reasoning ? renderVoteControls(e) : '';
        const weakStamp = (isConflict && e.votes && e.votes.net <= -3)
            ? '<span class="weak-stamp" title="The party finds thy excuse wanting.">🎲 Re-roll?</span>'
            : '';

        const rowClass = `day-event-row evt-${escapeHtml(e.status)}${isConflict ? ' conflict' : ''}`;

        return `
            <div class="${rowClass}">
                <div class="day-event-time">${timeGutter}</div>
                <div class="day-event-body">
                    <div class="day-event-header">
                        <span class="day-event-status">${escapeHtml(icon)} ${escapeHtml(status)}</span>
                        <span class="day-event-title">${escapeHtml(e.title)}</span>
                        ${weakStamp}
                    </div>
                    <div class="day-event-name">&mdash; ${escapeHtml(e.name)}</div>
                    ${whenSubtitle}
                    ${desc}
                    ${reasoningBlock}
                    ${voteControls}
                </div>
                <div class="day-event-actions">
                    <button type="button" class="day-event-edit"   data-edit-id="${escapeHtml(e.id)}">Edit</button>
                    <button type="button" class="day-event-delete" data-delete-id="${escapeHtml(e.id)}">Delete</button>
                </div>
            </div>
        `;
    }

    function renderDayList(dateIso) {
        // Show every event whose date range covers this day.
        const dayEvents = cachedEvents.filter(e => eventCoversDay(e, dateIso));
        const listEl = document.getElementById('day-event-list');
        document.getElementById('day-modal-date').textContent = formatHumanDate(dateIso);
        dayModal.dataset.date = dateIso;

        if (dayEvents.length === 0) {
            listEl.innerHTML = `
                <div class="day-event-empty">
                    No entries yet for this day.<br>
                    Be the first to make thy mark.
                </div>
            `;
            return;
        }

        const hasSession = dayEvents.some(e => e.status === 'session');
        const hasConflict = dayEvents.some(e => CONFLICTING_STATUSES.has(e.status));

        // Chronological: all-day first, then timed events by start_time.
        const sorted = [...dayEvents].sort((a, b) => {
            if (a.all_day !== b.all_day) return a.all_day ? -1 : 1;
            if (!a.all_day && !b.all_day) {
                const ta = a.start_time || '';
                const tb = b.start_time || '';
                if (ta !== tb) return ta < tb ? -1 : 1;
            }
            return (a.created_at || '').localeCompare(b.created_at || '');
        });

        const banner = (hasSession && hasConflict)
            ? `<div class="session-banner">⚔ A session is set for this day &mdash; others must justify their absence. ⚔</div>`
            : '';

        listEl.innerHTML = banner + sorted.map(e => renderEventRow(e, hasSession)).join('');
    }

    function openDayModal(dateIso) {
        renderDayList(dateIso);
        dayModal.classList.remove('hidden');
    }

    // ---------- Datetime form helpers ----------
    function syncTimeInputsEnabled() {
        const allDay = allDayCheckbox.checked;
        startTimeInput.disabled = allDay;
        endTimeInput.disabled = allDay;
        if (allDay) {
            startTimeInput.value = '';
            endTimeInput.value = '';
        } else {
            if (!startTimeInput.value) startTimeInput.value = '18:00';
            if (!endTimeInput.value) endTimeInput.value = '22:00';
        }
        updateDatetimeHint();
    }

    function updateDatetimeHint() {
        const start = startDateInput.value;
        const end = endDateInput.value;
        if (!start || !end) {
            datetimeHint.hidden = true;
            return;
        }
        if (end > start) {
            const days = isoDatesInRange(start, end).length;
            datetimeHint.textContent = `Spans ${days} days.`;
            datetimeHint.hidden = false;
        } else {
            datetimeHint.hidden = true;
        }
    }

    function getFormStatus() {
        const el = form.querySelector('input[name="status"]:checked');
        return el ? el.value : 'busy';
    }

    // ---------- Event (add / edit) modal ----------
    function updateReasoningVisibility() {
        const startIso = startDateInput.value;
        const endIso = endDateInput.value || startIso;
        const status = getFormStatus();
        const allDay = allDayCheckbox.checked;
        const editingId = document.getElementById('event-id').value;

        // If we're editing a session, we don't ourselves need reasoning even if
        // the days overlap another session (rare edge case).
        const editingEvt = editingId ? cachedEvents.find(e => String(e.id) === String(editingId)) : null;
        const editingIsSession = editingEvt && editingEvt.status === 'session';

        const needs =
            CONFLICTING_STATUSES.has(status) &&
            !editingIsSession &&
            startIso && endIso &&
            rangeOverlapsAnySession(startIso, endIso, allDay, startTimeInput.value, endTimeInput.value);

        const reasoningField = document.getElementById('reasoning-field');
        const reasoningTextarea = document.getElementById('event-reasoning');
        reasoningField.hidden = !needs;
        if (!needs) {
            reasoningTextarea.removeAttribute('required');
        } else {
            reasoningTextarea.setAttribute('required', 'required');
        }
    }

    function resetForm() {
        form.reset();
        clearError();
        document.getElementById('event-id').value = '';
        allDayCheckbox.checked = true;
        syncTimeInputsEnabled();
    }

    function openEventModalForAdd(dateIso) {
        resetForm();
        modalTitleEl.textContent = 'A New Entry in the Ledger';
        submitBtn.textContent = 'Inscribe';

        startDateInput.value = dateIso;
        endDateInput.value = dateIso;
        document.getElementById('event-modal-date').textContent = formatHumanDate(dateIso);

        document.getElementById('event-name').value = lastNameUsed;
        const busyRadio = form.querySelector('input[name="status"][value="busy"]');
        if (busyRadio) busyRadio.checked = true;

        updateDatetimeHint();
        updateReasoningVisibility();

        eventModal.classList.remove('hidden');
        setTimeout(() => {
            const nameField = document.getElementById('event-name');
            const titleField = document.getElementById('event-title');
            (nameField.value ? titleField : nameField).focus();
        }, 0);
    }

    function openEventModalForEdit(eventId) {
        const evt = cachedEvents.find(e => String(e.id) === String(eventId));
        if (!evt) return;

        resetForm();
        modalTitleEl.textContent = 'Amend the Entry';
        submitBtn.textContent = 'Amend';

        document.getElementById('event-id').value = evt.id;
        document.getElementById('event-modal-date').textContent = formatHumanDate(evt.start_date);

        document.getElementById('event-name').value = evt.name || '';
        document.getElementById('event-title').value = evt.title || '';
        document.getElementById('event-description').value = evt.description || '';
        document.getElementById('event-reasoning').value = evt.reasoning || '';
        startDateInput.value = evt.start_date;
        endDateInput.value = evt.end_date;
        allDayCheckbox.checked = !!evt.all_day;
        startTimeInput.value = evt.start_time || '';
        endTimeInput.value = evt.end_time || '';
        syncTimeInputsEnabled();

        const statusRadio = form.querySelector(`input[name="status"][value="${evt.status}"]`);
        if (statusRadio) statusRadio.checked = true;

        updateDatetimeHint();
        updateReasoningVisibility();

        dayModal.classList.add('hidden');
        eventModal.classList.remove('hidden');
        setTimeout(() => {
            const reasoningField = document.getElementById('reasoning-field');
            if (!reasoningField.hidden) {
                document.getElementById('event-reasoning').focus();
            } else {
                document.getElementById('event-title').focus();
            }
        }, 0);
    }

    function closeModals() {
        eventModal.classList.add('hidden');
        dayModal.classList.add('hidden');
    }

    // ---------- Submit / Delete / Vote handlers ----------
    async function submitEvent(e) {
        e.preventDefault();
        clearError();

        const eventId = document.getElementById('event-id').value;
        const isEdit = !!eventId;
        const allDay = allDayCheckbox.checked;

        const payload = {
            name:        document.getElementById('event-name').value.trim(),
            title:       document.getElementById('event-title').value.trim(),
            description: document.getElementById('event-description').value.trim(),
            reasoning:   document.getElementById('event-reasoning').value.trim(),
            status:      getFormStatus(),
            start_date:  startDateInput.value,
            end_date:    endDateInput.value || startDateInput.value,
            all_day:     allDay,
            start_time:  allDay ? null : startTimeInput.value,
            end_time:    allDay ? null : endTimeInput.value,
        };

        if (!payload.name)        { showError('Thy name is required.'); return; }
        if (!payload.title)       { showError('Pray, what befalls this day?'); return; }
        if (!payload.start_date)  { showError('A start date is required.'); return; }
        if (!payload.end_date)    { showError('An end date is required.'); return; }
        if (payload.end_date < payload.start_date) {
            showError('End date must not precede the start date.');
            return;
        }
        if (!allDay && (!payload.start_time || !payload.end_time)) {
            showError('Start and end times are required for timed events.');
            return;
        }

        // Mirror the server's overlap check so the user sees the warning early.
        if (
            CONFLICTING_STATUSES.has(payload.status) &&
            rangeOverlapsAnySession(payload.start_date, payload.end_date, payload.all_day, payload.start_time, payload.end_time) &&
            !payload.reasoning
        ) {
            showError('A session falls within this range — thou must provide a reasoning.');
            return;
        }

        setSubmitting(true, isEdit);
        try {
            if (isEdit) {
                await patchEvent(eventId, payload);
            } else {
                await createEvent(payload);
            }
            lastNameUsed = payload.name;
            try { localStorage.setItem('partyLedger.name', payload.name); } catch (_) {}
            const dateIso = payload.start_date;
            closeModals();
            await refreshCalendar();
            openDayModal(dateIso);
        } catch (err) {
            showError(err.message || 'The ink has spilled.');
        } finally {
            setSubmitting(false, isEdit);
        }
    }

    async function handleDelete(id) {
        if (!confirm('Strike this entry from the ledger?')) return;
        try {
            await deleteEventApi(id);
            await refreshCalendar();
            const dateIso = dayModal.dataset.date;
            if (dateIso && !dayModal.classList.contains('hidden')) {
                renderDayList(dateIso);
            }
        } catch (err) {
            alert(err.message || 'Could not delete entry.');
        }
    }

    async function handleVote(eventId, requestedValue) {
        const voter = ensureName('Thy name (to cast a vote):');
        if (!voter) return;

        const evt = cachedEvents.find(e => String(e.id) === String(eventId));
        if (!evt) return;
        const currentVote = (evt.votes && evt.votes.my_vote) || 0;
        const newValue = currentVote === requestedValue ? 0 : requestedValue;

        try {
            await castVote(eventId, voter, newValue);
            await refreshCalendar();
            const dateIso = dayModal.dataset.date;
            if (dateIso && !dayModal.classList.contains('hidden')) {
                renderDayList(dateIso);
            }
        } catch (err) {
            alert(err.message || 'Vote failed.');
        }
    }

    // ---------- Boot ----------
    document.addEventListener('DOMContentLoaded', async () => {
        // Ensure we have a session token before proceeding
        await ensureSessionToken();

        calendar = new FullCalendar.Calendar(calendarEl, {
            initialView: 'dayGridMonth',
            headerToolbar: {
                left:   'prev,next today',
                center: 'title',
                right:  'dayGridMonth,listMonth',
            },
            buttonText: {
                today: 'Today',
                month: 'Month',
                list:  'List',
            },
            firstDay: 0,
            dayMaxEvents: 3,
            moreLinkClick: 'popover',
            height: 'auto',
            navLinks: false,
            selectable: false,
            fixedWeekCount: false,
            displayEventTime: true,
            eventTimeFormat: { hour: 'numeric', minute: '2-digit', meridiem: 'short' },
            eventOrder: 'start,-duration,allDay,title',
            dateClick: (info) => openDayModal(info.dateStr),
            eventClick: (info) => {
                info.jsEvent.preventDefault();
                // For an all-day multi-day event, info.event.startStr is the start.
                // But the user clicked on whatever day the chip happens to sit on;
                // we just open the day modal for the event's start date as a sensible default.
                const dateIso = (info.event.startStr || '').slice(0, 10);
                openDayModal(dateIso);
            },
        });
        calendar.render();

        try {
            await refreshCalendar();
        } catch (err) {
            console.error(err);
        }
    });

    form.addEventListener('submit', submitEvent);

    form.querySelectorAll('input[name="status"]').forEach(radio => {
        radio.addEventListener('change', updateReasoningVisibility);
    });

    allDayCheckbox.addEventListener('change', syncTimeInputsEnabled);

    startDateInput.addEventListener('change', () => {
        // Bump end forward if it slipped behind the new start.
        if (endDateInput.value && endDateInput.value < startDateInput.value) {
            endDateInput.value = startDateInput.value;
        }
        updateDatetimeHint();
        updateReasoningVisibility();
    });
    endDateInput.addEventListener('change', () => {
        if (endDateInput.value && startDateInput.value && endDateInput.value < startDateInput.value) {
            endDateInput.value = startDateInput.value;
        }
        updateDatetimeHint();
        updateReasoningVisibility();
    });

    startTimeInput.addEventListener('change', updateReasoningVisibility);
    endTimeInput.addEventListener('change', updateReasoningVisibility);

    document.querySelectorAll('[data-close]').forEach(el => {
        el.addEventListener('click', closeModals);
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeModals();
    });

    document.getElementById('day-add-btn').addEventListener('click', () => {
        const dateIso = dayModal.dataset.date;
        if (!dateIso) return;
        dayModal.classList.add('hidden');
        openEventModalForAdd(dateIso);
    });

    document.getElementById('day-event-list').addEventListener('click', (e) => {
        const deleteBtn = e.target.closest('[data-delete-id]');
        if (deleteBtn) { handleDelete(deleteBtn.dataset.deleteId); return; }

        const editBtn = e.target.closest('[data-edit-id]');
        if (editBtn) { openEventModalForEdit(editBtn.dataset.editId); return; }

        const voteBtn = e.target.closest('.vote-btn');
        if (voteBtn) {
            const container = voteBtn.closest('.vote-controls');
            if (!container) return;
            const eventId = container.dataset.eventId;
            const value = parseInt(voteBtn.dataset.vote, 10);
            if (!eventId || isNaN(value)) return;
            handleVote(eventId, value);
        }
    });
})();
