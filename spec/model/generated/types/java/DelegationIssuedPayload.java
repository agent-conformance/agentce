package example;

/* metamodel_version: 1.11.0 */
/* version: 0.1.0 */
import java.net.URI;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZonedDateTime;
import java.util.List;
import lombok.*;

@Data
@EqualsAndHashCode(callSuper=false)
public class DelegationIssuedPayload extends Payload {

  private String tokenRef;
  private String issuer;
  private String subjectPrincipal;
  private String actorPrincipal;
  private List<Principal> chain;
  private List<String> scopeGranted;
  private List<String> scopeParent;
  private ZonedDateTime expires;
  private Verification verification;


}