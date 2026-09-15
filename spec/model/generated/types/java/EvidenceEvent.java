package example;

/* metamodel_version: 1.11.0 */
/* version: 0.1.0 */
import java.net.URI;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZonedDateTime;
import java.util.List;
import lombok.*;

/**
  CloudEvent envelope carrying an AgentCE evidence payload (SPEC 6.2.1).
**/
@Data
@EqualsAndHashCode(callSuper=false)
public abstract class EvidenceEvent  {

  private String specversion;
  private String id;
  private String source;
  private String type;
  private ZonedDateTime time;
  private String subject;
  private String datacontenttype;
  private String agentcetrace;
  private String agentcespan;
  private String agentceparent;
  private String agentcetask;
  private String agentcesourceclass;
  private String agentceconv;
  private Payload data;


}