"""好友关系与好友申请测试。"""
from django.test import TestCase

from backend.accounts.models import User
from backend.contacts.models import FriendRequest, Friendship
from backend.tests_utils import api_post, befriend, data_of, error_code, make_client, make_user


class FriendRequestTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice", "爱丽丝")
        self.bob = make_user("bob", "小鲍")
        self.alice_client = make_client(self.alice)
        self.bob_client = make_client(self.bob)

    def test_send_request_creates_pending(self):
        response = api_post(self.alice_client, "/api/v1/friend-requests/", {
            "user_id": self.bob.id, "message": "你好",
        })
        self.assertEqual(response.status_code, 201)
        item = FriendRequest.objects.get()
        self.assertEqual(item.status, FriendRequest.STATUS_PENDING)
        self.assertEqual(item.message, "你好")

    def test_cannot_add_self(self):
        response = api_post(self.alice_client, "/api/v1/friend-requests/", {
            "user_id": self.alice.id,
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(error_code(response), "self_request")

    def test_duplicate_pending_rejected(self):
        api_post(self.alice_client, "/api/v1/friend-requests/", {"user_id": self.bob.id})
        response = api_post(self.alice_client, "/api/v1/friend-requests/", {"user_id": self.bob.id})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(error_code(response), "request_pending")

    def test_already_friends_rejected(self):
        befriend(self.alice, self.bob)
        response = api_post(self.alice_client, "/api/v1/friend-requests/", {"user_id": self.bob.id})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(error_code(response), "already_friends")

    def test_accept_creates_friendship(self):
        request = FriendRequest.objects.create(from_user=self.alice, to_user=self.bob)
        response = api_post(self.bob_client, f"/api/v1/friend-requests/{request.id}/accept/")
        self.assertEqual(response.status_code, 200)
        request.refresh_from_db()
        self.assertEqual(request.status, FriendRequest.STATUS_ACCEPTED)
        self.assertTrue(Friendship.are_friends(self.alice.id, self.bob.id))
        # 好友关系只存一行，且小 id 在前
        self.assertEqual(Friendship.objects.count(), 1)

    def test_reject_does_not_create_friendship(self):
        request = FriendRequest.objects.create(from_user=self.alice, to_user=self.bob)
        response = api_post(self.bob_client, f"/api/v1/friend-requests/{request.id}/reject/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Friendship.are_friends(self.alice.id, self.bob.id))

    def test_only_recipient_can_handle(self):
        request = FriendRequest.objects.create(from_user=self.alice, to_user=self.bob)
        # alice 是发起人，不是收件人
        response = api_post(self.alice_client, f"/api/v1/friend-requests/{request.id}/accept/")
        self.assertEqual(response.status_code, 404)

    def test_handling_twice_conflicts(self):
        request = FriendRequest.objects.create(from_user=self.alice, to_user=self.bob)
        api_post(self.bob_client, f"/api/v1/friend-requests/{request.id}/accept/")
        response = api_post(self.bob_client, f"/api/v1/friend-requests/{request.id}/accept/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(error_code(response), "already_handled")

    def test_cross_request_auto_accepts(self):
        """对方已向我申请时，我再申请则直接互为好友（微信式直觉）。"""
        FriendRequest.objects.create(from_user=self.bob, to_user=self.alice)
        response = api_post(self.alice_client, "/api/v1/friend-requests/", {"user_id": self.bob.id})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data_of(response)["auto_accepted"])
        self.assertTrue(Friendship.are_friends(self.alice.id, self.bob.id))

    def test_incoming_list_shows_requests(self):
        FriendRequest.objects.create(from_user=self.alice, to_user=self.bob)
        response = self.bob_client.get("/api/v1/friend-requests/?direction=incoming")
        requests = data_of(response)["requests"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["direction"], "incoming")
        self.assertEqual(requests[0]["from_user"]["id"], self.alice.id)


class FriendListTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice", "爱丽丝")
        self.bob = make_user("bob", "小鲍")
        self.carol = make_user("carol", "卡罗")
        self.alice_client = make_client(self.alice)

    def test_friend_list_bidirectional(self):
        befriend(self.alice, self.bob)
        mine = data_of(self.alice_client.get("/api/v1/friends/"))["friends"]
        self.assertEqual([f["id"] for f in mine], [self.bob.id])
        # 对方也应看到我
        bob_friends = data_of(make_client(self.bob).get("/api/v1/friends/"))["friends"]
        self.assertEqual([f["id"] for f in bob_friends], [self.alice.id])

    def test_remove_friend(self):
        befriend(self.alice, self.bob)
        response = self.alice_client.delete(f"/api/v1/friends/{self.bob.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Friendship.are_friends(self.alice.id, self.bob.id))

    def test_remove_non_friend_404(self):
        response = self.alice_client.delete(f"/api/v1/friends/{self.carol.id}/")
        self.assertEqual(response.status_code, 404)

    def test_remove_friend_clears_pending_requests(self):
        befriend(self.alice, self.bob)
        request = FriendRequest.objects.create(from_user=self.alice, to_user=self.bob)
        self.alice_client.delete(f"/api/v1/friends/{self.bob.id}/")
        request.refresh_from_db()
        self.assertEqual(request.status, FriendRequest.STATUS_REJECTED)

    def test_users_cannot_see_others_friends(self):
        befriend(self.alice, self.bob)
        carol_friends = data_of(make_client(self.carol).get("/api/v1/friends/"))["friends"]
        self.assertEqual(carol_friends, [])
