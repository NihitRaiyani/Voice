from xml.etree import ElementTree

from roma.telephony.twiml import connect_stream_twiml


def test_connect_stream_twiml_builds_a_bidirectional_stream():
    root = ElementTree.fromstring(connect_stream_twiml("wss://voice.example/ws"))

    assert root.tag == "Response"
    connect = root.find("Connect")
    assert connect is not None
    stream = connect.find("Stream")
    assert stream is not None
    assert stream.attrib == {"url": "wss://voice.example/ws"}


def test_connect_stream_twiml_carries_an_escaped_lead_as_a_parameter():
    lead_token = 'lead<&"value'
    root = ElementTree.fromstring(
        connect_stream_twiml("wss://voice.example/ws", lead_token=lead_token)
    )

    stream = root.find("./Connect/Stream")
    assert stream is not None
    assert stream.attrib == {"url": "wss://voice.example/ws"}
    parameter = stream.find("Parameter")
    assert parameter is not None
    assert parameter.attrib == {"name": "lead", "value": lead_token}
