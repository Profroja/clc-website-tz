from datetime import date, timedelta

from django.core import mail
from django.test import TestCase, override_settings

from accounts.models import User
from appointments import availability
from appointments.models import Appointment
from core.models import ServicePackage


def next_weekday(weekday, min_days=3):
    """The next date (at least `min_days` away, but inside the booking window) falling on a Python weekday (Mon=0)."""
    day = availability.now_local().date() + timedelta(days=min_days)
    while day.weekday() != weekday:
        day += timedelta(days=1)
    return day


class AppointmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('boss@clc.tz', 'Pw-12345-xyz', full_name='Boss')  # also a CLC Admin
        self.monday = next_weekday(0)
        mail.outbox.clear()

    def body(self, day=None, time='10:00', **over):
        return {'full_name': 'Neema Lyimo', 'phone': '0745118253', 'email': 'neema@example.com', 'service': 'Wills',
                'meeting_kind': 'google_meet', 'date': str(day or self.monday), 'time': time, 'language': 'sw', **over}

    def book(self, **kw):
        return self.client.post('/api/appointments/', self.body(**kw), content_type='application/json')

    def admin_headers(self):
        login = self.client.post('/api/auth/login/', {'email': 'boss@clc.tz', 'password': 'Pw-12345-xyz'}, content_type='application/json').json()
        token = self.client.post('/api/auth/select-role/', {'membership_id': login['memberships'][0]['id']}, content_type='application/json',
                                 HTTP_AUTHORIZATION=f"Bearer {login['pick_role_token']}").json()['access']
        return {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    # --- what can be booked
    def test_options_and_days(self):
        ServicePackage.objects.create(title='Wosia')
        data = self.client.get('/api/appointments/options/').json()
        self.assertEqual(data['services'], ['Wosia'])
        self.assertEqual([k['value'] for k in data['meeting_kinds']], ['whatsapp', 'phone', 'google_meet', 'in_person'])
        days = {d['date']: d for d in data['days']}
        self.assertFalse(days[str(next_weekday(6))]['open'])  # Sunday is closed
        self.assertTrue(days[str(self.monday)]['open'])
        self.assertEqual(days[str(self.monday)]['free'], 18)

    def test_slots_follow_the_office_hours(self):
        monday = self.client.get('/api/appointments/slots/', {'date': str(self.monday)}).json()
        self.assertEqual((monday[0]['time'], monday[-1]['time'], len(monday)), ('09:00', '17:30', 18))  # Mon-Fri 9:00-18:00
        saturday = self.client.get('/api/appointments/slots/', {'date': str(next_weekday(5))}).json()
        self.assertEqual((saturday[0]['time'], saturday[-1]['time'], len(saturday)), ('09:00', '12:30', 8))  # Sat 9:00-13:00
        self.assertEqual(self.client.get('/api/appointments/slots/', {'date': str(next_weekday(6))}).json(), [])
        self.assertEqual(self.client.get('/api/appointments/slots/').status_code, 400)

    # --- booking
    def test_booking_sends_both_emails_and_takes_the_slot(self):
        res = self.book()
        self.assertEqual(res.status_code, 201, res.content)
        data = res.json()
        self.assertRegex(data['reference'], r'^APT/\d{4}/0001$')
        self.assertEqual((data['time'], data['phone'], data['confirmation_emailed']), ('10:00', '+255745118253', True))

        client_mail = next(m for m in mail.outbox if m.to == ['neema@example.com'])
        self.assertIn(data['reference'], client_mail.subject)
        self.assertIn('Appointment yako', client_mail.subject)  # Swahili
        self.assertIn('Google Meet', client_mail.body)
        admin_mail = next(m for m in mail.outbox if 'boss@clc.tz' in m.to)
        self.assertIn('Neema Lyimo', admin_mail.body)
        self.assertEqual(admin_mail.reply_to, ['neema@example.com'])

        slot = next(s for s in self.client.get('/api/appointments/slots/', {'date': str(self.monday)}).json() if s['time'] == '10:00')
        self.assertFalse(slot['available'])
        again = self.book(full_name='Someone Else', email='else@example.com')
        self.assertEqual((again.status_code, again.json()['code']), (409, 'slot_taken'))

    def test_english_confirmation(self):
        self.book(language='en', meeting_kind='in_person')
        client_mail = next(m for m in mail.outbox if m.to == ['neema@example.com'])
        self.assertIn('Your CLC appointment is confirmed', client_mail.subject)
        self.assertIn('P.O. Box 4661', client_mail.body)

    def test_validation(self):
        res = self.book(full_name='', phone='abc', email='not-an-email', meeting_kind='teleport')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(set(res.json()['errors']), {'full_name', 'phone', 'email', 'meeting_kind'})
        self.assertEqual(self.book(time='soon').status_code, 400)
        self.assertEqual(len(mail.outbox), 0)  # nothing is sent for a refused booking

    def test_only_real_future_slots_can_be_booked(self):
        self.assertEqual(self.book(day=next_weekday(6)).status_code, 409)  # Sunday: closed
        self.assertEqual(self.book(time='08:00').status_code, 409)  # before opening
        self.assertEqual(self.book(time='18:00').status_code, 409)  # after the last slot
        self.assertEqual(self.book(time='10:15').status_code, 409)  # not on the half hour
        self.assertEqual(self.book(day=availability.now_local().date() - timedelta(days=1)).status_code, 409)  # the past
        self.assertEqual(self.book(day=availability.now_local().date() + timedelta(days=60)).status_code, 409)  # too far ahead
        self.assertEqual(Appointment.objects.count(), 0)

    def test_a_mail_failure_does_not_undo_the_booking(self):
        from unittest import mock
        with mock.patch('django.core.mail.message.EmailMessage.send', side_effect=OSError('smtp down')):
            res = self.book()
        self.assertEqual(res.status_code, 201)
        self.assertFalse(res.json()['confirmation_emailed'])
        self.assertEqual(Appointment.objects.count(), 1)

    @override_settings(APPOINTMENT_NOTIFY_EMAILS=['reception@clc.tz'])
    def test_extra_notification_addresses(self):
        self.book()
        admin_mail = next(m for m in mail.outbox if 'boss@clc.tz' in m.to)
        self.assertEqual(sorted(admin_mail.to), ['boss@clc.tz', 'reception@clc.tz'])

    # --- admin
    def test_admin_list_and_status_changes(self):
        self.assertEqual(self.client.get('/api/admin/appointments/').status_code, 401)
        appt = self.book().json()
        auth = self.admin_headers()
        rows = self.client.get('/api/admin/appointments/', **auth).json()
        self.assertEqual([(r['reference'], r['status']) for r in rows], [(appt['reference'], 'scheduled')])

        url = f"/api/admin/appointments/{appt['id']}/"
        patch = lambda body: self.client.patch(url, body, content_type='application/json', **auth)
        self.assertEqual(patch({'status': 'scheduled'}).status_code, 400)
        mail.outbox.clear()
        done = patch({'status': 'cancelled', 'note': 'The advocate is unavailable'})
        self.assertEqual((done.status_code, done.json()['status']), (200, 'cancelled'))
        cancel_mail = next(m for m in mail.outbox if m.to == ['neema@example.com'])
        self.assertIn('imeghairiwa', cancel_mail.subject)
        self.assertIn('The advocate is unavailable', cancel_mail.body)
        self.assertEqual(patch({'status': 'completed'}).status_code, 409)  # already closed
        # the cancelled slot can be booked again
        self.assertEqual(self.book(full_name='Next Person', email='next@example.com').status_code, 201)

    def test_marking_completed_sends_no_email(self):
        appt = self.book().json()
        mail.outbox.clear()
        self.client.patch(f"/api/admin/appointments/{appt['id']}/", {'status': 'completed'}, content_type='application/json', **self.admin_headers())
        self.assertEqual(len(mail.outbox), 0)

    def test_admin_assigns_an_advocate(self):
        from accounts.models import Membership, Organization
        firm = Organization.objects.create(type='law_firm', name='PNJ Legal', status='approved')
        adv = Membership.objects.create(user=User.objects.create_user('neema@pnj.co.tz', 'Pw-12345-xyz', full_name='Neema Lyimo'),
                                        organization=firm, role='advocate', status='active')
        pending = Organization.objects.create(type='law_firm', name='Pending LLP', status='pending')
        stranger = Membership.objects.create(user=User.objects.create_user('x@p.co.tz', 'Pw-12345-xyz', full_name='X'),
                                             organization=pending, role='advocate', status='active')
        appt = self.book().json()
        auth = self.admin_headers()
        url = f"/api/admin/appointments/{appt['id']}/"
        patch = lambda body: self.client.patch(url, body, content_type='application/json', **auth)
        self.assertEqual(patch({'advocate_id': 'nope'}).status_code, 400)  # not even an id
        self.assertEqual(patch({'advocate_id': str(stranger.id)}).status_code, 400)  # the firm is not approved
        ok = patch({'advocate_id': str(adv.id)}).json()
        self.assertEqual((ok['advocate_name'], ok['status']), ('Neema Lyimo', 'scheduled'))  # assigning does not change the status
        self.assertEqual(self.client.get('/api/admin/appointments/', **auth).json()[0]['advocate_name'], 'Neema Lyimo')
        self.assertEqual(patch({'advocate_id': None}).json()['advocate_name'], None)  # and it can be undone
