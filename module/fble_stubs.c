/* SPDX-License-Identifier: MPL-2.0 AND Apache-2.0
 * Copyright (C) 2026 Jacek Mariański
 * The behaviour kept here (update_bt_current_status's report, below) is read from the JieLi AC79 SDK's btstack.a
 * (avctp_user.c), Copyright (c) JIELI (Zhuhai Jieli Technology Co., Ltd.), Apache License 2.0. */
/* fble's diet (milestone M5, build_module.py lever "bredr"): JieLi's BR/EDR, audio, TWS, ISO and classic-profile
 * objects are left out of the libraries the module links (no classic Bluetooth in it: the SDK's own config says
 * TCFG_USER_BT_CLASSIC_ENABLE 0, yet the BLE code still names them). These are what the BLE code still calls after
 * LTO (the link's own list of undefined references), each answered as the original answers when there is no
 * classic link, no TWS pair, no ANCS client: nothing done, 0 or NULL. Read from each original's IR (bitcode),
 * docs/BLE_MODULE.md "M5: dieta". */
#include <stdint.h>
#include <stddef.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;

/* ---- avctp_user.c: the classic links' bookkeeping (a table of classic connections, empty here) */
extern void bt_event_update_to_user(u8 *addr, u8 type, u8 event, u32 value);   /* user_interface.c: kept */
/* the one with a duty: the stack's state to the application (BT_STATUS_INIT_OK among them: the module's "BT
 * ready", shim_bt_event). The original, with no classic link for addr (or none given): the new state, if any, as an
 * event from BT_EVENT_FROM_CON (3), with the role 0xAA (unknown); its call to stack_send_infor_2_user is empty */
void update_bt_current_status(u8 *addr, u8 new_status, u8 conn_status)
{
    (void)conn_status;
    if (new_status)
        bt_event_update_to_user(addr, 3, new_status, 0xAA);
}
u8 b_create_bt_new_conn(u8 *addr, u16 handle) { (void)addr, (void)handle; return 0; }
void update_connectiong_mac_addr(u8 *addr) { (void)addr; }
u8 free_conn_for_addr(u8 *addr, u8 all) { (void)addr, (void)all; return 0; }
u16 get_emitter_curr_channel_state(void) { return 0; }   /* no classic link: 0, as the original */
void clear_sniff_cnt(void) {}
u8 get_is_in_background_flag(void) { return 0; }          /* its flag is set only by classic profiles */

/* ---- bt_audio.c: SCO audio */
void aec_sco_connection_start(u8 type, u16 n, u8 *addr) { (void)type, (void)n, (void)addr; }
void aec_sco_connection_stop(void) {}

/* ---- ancs_client.c, ams_client.c: Apple's notification and media services, as a GATT client. Never started now:
 * the original init (at every stack start) registered an HCI event handler that looked for ANCS on each link */
int ancs_client_btstack_init_for_check(void) { return 0; }
void ancs_client_exit(void) {}
void ams_client_exit(void) {}
void ancs_handle_schedule(void) {}
void ams_handle_schedule(void) {}

/* ---- sdp_util.c (weak there too) */
void sdp_callback_remote_type(u8 type) { (void)type; }

/* ---- lmp*.c: the classic link manager. Handles below get_ble_link_handle() are classic ones: none exist */
void *lmp_private_remote_addr_for_handler(u32 handle) { (void)handle; return NULL; }
u16 lmp_private_handler_for_remote_addr(u8 *addr, u32 type) { (void)addr, (void)type; return 0; }
void *lmp_private_get_tx_packet_buffer(u32 len) { (void)len; return NULL; }
int lmp_hci_send_packet(u8 *packet, u32 size) { (void)packet, (void)size; return 0; }
void lmp_hci_private_free_acl_packet(u8 *packet) { (void)packet; }
void lmp_hci_private_try_free_acl_packet(u8 *packet) { (void)packet; }
void lmp_hci_host_num_of_completed_packets(u16 handle, u16 n) { (void)handle, (void)n; }
int lmp_hci_disconnect(u16 handle, u8 reason) { (void)handle, (void)reason; return 0; }
int lmp_hci_set_connection_encryption(u16 handle, u32 on) { (void)handle, (void)on; return 0; }
void lmp_hci_write_link_supervision_timeout(u16 handle, u32 t) { (void)handle, (void)t; }
void lmp_hci_create_connection(u8 *addr, u16 type, u8 mode, u8 r, u16 clk, u8 role)
{
    (void)addr, (void)type, (void)mode, (void)r, (void)clk, (void)role;
}
void lmp_hci_accept_connection_request(u8 *addr, u8 role) { (void)addr, (void)role; }
void lmp_hci_accept_sco_connection_request(u8 *addr, u32 a, u32 b, u16 c, u16 d, u8 e, u16 f)
{
    (void)addr, (void)a, (void)b, (void)c, (void)d, (void)e, (void)f;
}
void lmp_hci_reject_connection_request(u8 *addr, u8 reason) { (void)addr, (void)reason; }
void lmp_hci_io_capability_request_reply(u8 *addr, u8 io, u8 oob, u8 auth) { (void)addr, (void)io, (void)oob, (void)auth; }
void lmp_hci_link_key_request_reply(u8 *addr, u8 *key) { (void)addr, (void)key; }
void lmp_hci_link_key_request_negative_reply(u8 *addr) { (void)addr; }
void lmp_hci_pin_code_request_reply(u8 *addr, u8 len, u8 *pin) { (void)addr, (void)len, (void)pin; }

/* ---- tws_ble_link.c: TWS over BLE (empty there as well: CONFIG_BTCTLER_TWS_ENABLE 0) */
int tws_link_get_ble_role_slave(void) { return 0; }
void tws_link_ble_slave_conn_establish_probe_handler(void) {}
void tws_link_ble_slave_conn_establish_handler(void) {}
void tws_phone_ble_link_connection(void *link) { (void)link; }
void tws_phone_ble_link_disconnection(void *link) { (void)link; }

/* ---- remote_device_db.c: classic link keys and the profiles each remote supports (in the store), none here:
 * a key not found (0), nothing kept, nothing to delete */
u8 last_device_connect_linkkey[16];               /* hci_vendor.c copies a classic link key into it */
int get_link_key(const u8 *addr, u8 *key, u8 id) { (void)addr, (void)key, (void)id; return 0; }
void put_link_key(const u8 *addr, const u8 *key, u8 sr, u8 id) { (void)addr, (void)key, (void)sr, (void)id; }
void delete_link_key(const u8 *addr, u8 id) { (void)addr, (void)id; }
void updata_last_link_key(const u8 *addr, u8 id) { (void)addr, (void)id; }
int get_support_profile(const u8 *addr, u8 id) { (void)addr, (void)id; return 0; }
u8 handle_remote_dev_type(const u8 *addr, u8 param) { (void)addr, (void)param; return 0; }
